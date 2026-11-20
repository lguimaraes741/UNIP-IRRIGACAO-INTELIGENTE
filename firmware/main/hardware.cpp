#include "hardware.hpp"
#include "bench_pins.hpp"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

using namespace irrigation;
namespace hardware {
static constexpr const char* TAG="hardware";
static constexpr auto hx_dout=static_cast<gpio_num_t>(bench::hx_dout),hx_sck=static_cast<gpio_num_t>(bench::hx_sck);
static constexpr auto dht_pin=static_cast<gpio_num_t>(bench::dht_data);
static constexpr auto dht_vcc=static_cast<gpio_num_t>(bench::dht_power);
static constexpr int64_t dht_off_ms=1500,dht_settle_ms=2500;  // power-off time; DHT22 needs ~1-2 s after power-up
static constexpr uint32_t dht_cycle_after=3;                   // consecutive misses before a power cycle
static volatile bool pumps_on=false;static volatile int64_t pumps_off_at_ms=0;
static constexpr double shunt_ohm=0.1; // R100 seen on the module; calibration still pending.
static portMUX_TYPE timing=portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t lock=nullptr;
static Snapshot shared;
static ScaleWindow<10> window; // ~1 s at the HX711 10 SPS rate.
static adc_oneshot_unit_handle_t adc=nullptr;
static std::array<adc_channel_t,3> soil_channels{};
static i2c_master_dev_handle_t ina=nullptr;
static volatile bool fast_power=false,kick_power=false;
static PowerPeaks peaks;

static int64_t now_ms(){return esp_timer_get_time()/1000;}

void outputs_low(){
 for(int pin:bench::output_pins){auto gpio=static_cast<gpio_num_t>(pin);gpio_reset_pin(gpio);gpio_set_level(gpio,0);gpio_set_direction(gpio,GPIO_MODE_OUTPUT);gpio_set_level(gpio,0);}
}

// 24 data pulses + 1 (channel A, gain 128). SCK must stay high < 60 us.
static uint32_t hx_read(){
 uint32_t value=0;
 portENTER_CRITICAL(&timing);
 for(int i=0;i<24;++i){gpio_set_level(hx_sck,1);esp_rom_delay_us(1);value=(value<<1)|static_cast<uint32_t>(gpio_get_level(hx_dout));gpio_set_level(hx_sck,0);esp_rom_delay_us(1);}
 gpio_set_level(hx_sck,1);esp_rom_delay_us(1);gpio_set_level(hx_sck,0);
 portEXIT_CRITICAL(&timing);
 return value;
}

static bool ina_register(uint8_t reg,uint16_t& out){
 uint8_t data[2];
 if(i2c_master_transmit_receive(ina,&reg,1,data,2,50)!=ESP_OK)return false;
 out=static_cast<uint16_t>((data[0]<<8)|data[1]);return true;
}
static void ina_init(){
 i2c_master_bus_config_t bus_cfg={};bus_cfg.i2c_port=I2C_NUM_0;bus_cfg.sda_io_num=static_cast<gpio_num_t>(bench::i2c_sda);
 bus_cfg.scl_io_num=static_cast<gpio_num_t>(bench::i2c_scl);bus_cfg.clk_source=I2C_CLK_SRC_DEFAULT;bus_cfg.glitch_ignore_cnt=7;bus_cfg.flags.enable_internal_pullup=true;
 i2c_master_bus_handle_t bus;if(i2c_new_master_bus(&bus_cfg,&bus)!=ESP_OK){ESP_LOGE(TAG,"I2C bus unavailable");return;}
 i2c_device_config_t dev_cfg={};dev_cfg.dev_addr_length=I2C_ADDR_BIT_LEN_7;dev_cfg.device_address=bench::ina_address;dev_cfg.scl_speed_hz=100000;
 if(i2c_master_bus_add_device(bus,&dev_cfg,&ina)!=ESP_OK){ina=nullptr;return;}
 // Three channels, 16-sample average, 1.1 ms conversions, continuous shunt+bus.
 const uint8_t config[3]={0x00,0x77,0x27};
 if(i2c_master_transmit(ina,config,sizeof(config),50)!=ESP_OK)ESP_LOGW(TAG,"INA3221 configuration not acknowledged");
}
static void ina_sample(Snapshot& s){
 uint16_t manufacturer=0,die=0;
 s.ina_ok=ina&&ina_register(0xFE,manufacturer)&&ina_register(0xFF,die)&&ina3221_identity_ok(manufacturer,die);
 for(size_t ch=0;ch<3;++ch){
  uint16_t shunt=0,bus=0;s.power[ch].reset();
  // Identity is re-read every cycle so a glitched bus never yields plausible-looking values.
  if(s.ina_ok&&ina_register(static_cast<uint8_t>(1+2*ch),shunt)&&ina_register(static_cast<uint8_t>(2+2*ch),bus))s.power[ch]=ina3221_decode(shunt,bus,shunt_ohm);
 }
 s.power_at_ms=now_ms();
}

static void soil_init(){
 adc_oneshot_unit_init_cfg_t unit={};unit.unit_id=ADC_UNIT_1;
 if(adc_oneshot_new_unit(&unit,&adc)!=ESP_OK){adc=nullptr;return;}
 adc_oneshot_chan_cfg_t channel={};channel.atten=ADC_ATTEN_DB_12;channel.bitwidth=ADC_BITWIDTH_12;
 for(size_t i=0;i<3;++i){
  adc_unit_t unit_id;
  if(adc_oneshot_io_to_channel(bench::soil_pins[i],&unit_id,&soil_channels[i])!=ESP_OK||unit_id!=ADC_UNIT_1||adc_oneshot_config_channel(adc,soil_channels[i],&channel)!=ESP_OK){adc=nullptr;return;}
 }
}
static void soil_sample(Snapshot& s){
 for(size_t i=0;i<3;++i){
  int sum=0,raw=0;bool ok=adc!=nullptr;
  for(int n=0;ok&&n<16;++n){ok=adc_oneshot_read(adc,soil_channels[i],&raw)==ESP_OK;sum+=raw;}
  s.soil_raw[i]=ok?sum/16:-1;
 }
 s.soil_at_ms=now_ms();
}

static bool dht_wait(int level,int64_t timeout_us,int64_t& elapsed){
 int64_t start=esp_timer_get_time();
 while(gpio_get_level(dht_pin)!=level){if(esp_timer_get_time()-start>timeout_us)return false;}
 elapsed=esp_timer_get_time()-start;return true;
}
static std::optional<Climate> dht_read(){
 std::array<uint8_t,5> frame{};int64_t t=0;bool ok=true;
 gpio_set_level(dht_pin,0);esp_rom_delay_us(1200); // Host start signal >= 1 ms.
 portENTER_CRITICAL(&timing);
 gpio_set_level(dht_pin,1);
 ok=dht_wait(0,200,t)&&dht_wait(1,120,t)&&dht_wait(0,120,t);
 for(int bit=0;ok&&bit<40;++bit){
  ok=dht_wait(1,100,t)&&dht_wait(0,120,t);
  if(ok&&t>40)frame[bit/8]|=static_cast<uint8_t>(0x80>>(bit%8)); // ~27 us = 0, ~70 us = 1.
 }
 portEXIT_CRITICAL(&timing);
 gpio_set_level(dht_pin,1);
 return ok?dht22_decode(frame):std::nullopt;
}

// VCC and data both low while off, so the sensor is not fed through the data pull-up.
static void dht_power_off(){gpio_set_pull_mode(dht_pin,GPIO_FLOATING);gpio_set_level(dht_pin,0);gpio_set_level(dht_vcc,0);}
static void dht_power_on(){gpio_set_level(dht_vcc,1);gpio_set_pull_mode(dht_pin,GPIO_PULLUP_ONLY);gpio_set_level(dht_pin,1);}
static int64_t dht_powered_off_until=0;

static void task(void*){
 esp_task_wdt_add(nullptr);
 int64_t next_slow=0,next_dht=now_ms()+2000,last_ready=now_ms();
 for(;;){
  int64_t now=now_ms();
  if(gpio_get_level(hx_dout)==0){
   uint32_t raw=hx_read();last_ready=now;
   xSemaphoreTake(lock,portMAX_DELAY);
   if(hx711_saturated(raw))++shared.scale_faults;else{window.add(now,hx711_offset_binary(raw));++shared.scale_samples;}
   xSemaphoreGive(lock);
  }else if(now-last_ready>500){
   // No conversion for 500 ms: discard the window so stale counts are never reported.
   xSemaphoreTake(lock,portMAX_DELAY);window.reset();++shared.scale_faults;xSemaphoreGive(lock);last_ready=now;
  }
  if(now>=next_slow||kick_power){kick_power=false;Snapshot partial;ina_sample(partial);soil_sample(partial);
   xSemaphoreTake(lock,portMAX_DELAY);shared.ina_ok=partial.ina_ok;shared.power=partial.power;shared.power_at_ms=partial.power_at_ms;shared.soil_raw=partial.soil_raw;shared.soil_at_ms=partial.soil_at_ms;
   for(size_t ch=0;ch<3;++ch)if(partial.power[ch]){auto&p=*partial.power[ch];
    if(!(p.current_a<=peaks.peak_current_a[ch]))peaks.peak_current_a[ch]=p.current_a;
    if(!(p.bus_v>=peaks.min_bus_v[ch]))peaks.min_bus_v[ch]=p.bus_v;}
   if(partial.ina_ok)++peaks.samples;
   xSemaphoreGive(lock);
   next_slow=now+(fast_power?100:1000);}
  if(dht_powered_off_until&&now>=dht_powered_off_until){dht_power_on();dht_powered_off_until=0;next_dht=now+dht_settle_ms;}
  // Skip reads while a pump runs and for 2 s after (motor noise corrupts frames and can latch the sensor).
  bool quiet=!pumps_on&&now-pumps_off_at_ms>=2000;
  if(!dht_powered_off_until&&quiet&&now>=next_dht){auto climate=dht_read();
   xSemaphoreTake(lock,portMAX_DELAY);if(climate){shared.climate=climate;shared.climate_at_ms=now_ms();shared.climate_streak=0;}else{++shared.climate_faults;++shared.climate_streak;}
   uint32_t streak=shared.climate_streak;bool cycle=streak&&streak%dht_cycle_after==0;if(cycle)++shared.climate_resets;xSemaphoreGive(lock);
   next_dht=now+3000;
   // 10-11/11: the latched sensor only recovered after losing power; cut VCC (GPIO13) and data.
   if(cycle){ESP_LOGW(TAG,"DHT22: %u failed reads; power-cycling via GPIO%d",static_cast<unsigned>(streak),bench::dht_power);dht_power_off();dht_powered_off_until=now+dht_off_ms;}}
  esp_task_wdt_reset();
  vTaskDelay(pdMS_TO_TICKS(10));
 }
}

void start(){
 lock=xSemaphoreCreateMutex();
 gpio_reset_pin(hx_sck);gpio_set_direction(hx_sck,GPIO_MODE_OUTPUT);gpio_set_level(hx_sck,0);
 gpio_reset_pin(hx_dout);gpio_set_direction(hx_dout,GPIO_MODE_INPUT);
 gpio_reset_pin(dht_pin);gpio_set_direction(dht_pin,GPIO_MODE_INPUT_OUTPUT_OD);
 gpio_reset_pin(dht_vcc);gpio_set_direction(dht_vcc,GPIO_MODE_OUTPUT);
 // Boot also power-cycles the sensor (clears a latch from before the reset); first read after settling.
 dht_power_off();dht_powered_off_until=now_ms()+dht_off_ms;
 ina_init();soil_init();
 xTaskCreatePinnedToCore(task,"sensors",4096,nullptr,5,nullptr,1);
}

void power_fast(bool on){fast_power=on;kick_power=on;}
void pumps_active(bool on){if(pumps_on&&!on)pumps_off_at_ms=now_ms();pumps_on=on;}
PowerPeaks take_peaks(){xSemaphoreTake(lock,portMAX_DELAY);PowerPeaks out=peaks;peaks=PowerPeaks{};xSemaphoreGive(lock);return out;}

Snapshot snapshot(){
 xSemaphoreTake(lock,portMAX_DELAY);
 Snapshot s=shared;int64_t now=now_ms();s.now_ms=now;
 s.scale_age_ms=window.age_ms(now);
 s.scale_fresh=window.fresh(now,500);
 if(auto median=window.median())s.scale_counts=*median;
 if(auto span=window.span())s.scale_span=static_cast<int32_t>(*span);
 xSemaphoreGive(lock);
 return s;
}
}

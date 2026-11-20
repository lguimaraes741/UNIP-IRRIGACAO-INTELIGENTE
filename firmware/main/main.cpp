#include "controller.hpp"
#include "retention.hpp"
#include "durable_buffer.hpp"
#include "shared_rig.hpp"
#include "measurements.hpp"
#include "hardware.hpp"
#include "bench_pins.hpp"
#include "driver/gpio.h"
#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "mqtt_client.h"
#include "nvs_flash.h"
#include <cstring>
#include <ctime>
#include <cstdlib>
#include <cctype>
#include <atomic>
#include <cstddef>

using namespace irrigation;
static constexpr const char* TAG="irrigation";
// Physical cycles (14/11/2026): unit -> provisioned output (out1..3); an esp_timer cuts the GPIO at the
// unit's calibrated dose time (dose1..3 ms, from the wet flow test). The controller's own off (whole
// seconds) is the backup. Nothing actuates unless `irrigation=1` and every calibration is provisioned.
[[maybe_unused]] static bool cycle_on(size_t unit);
[[maybe_unused]] static void cycle_off();
struct BenchPump:ChannelDriver{
 int channel=-1;
 void off_all()override{
#if !CONFIG_IRRIGATION_SIMULATION
  cycle_off();
#endif
  channel=-1;}
 bool on(size_t index)override{
  if(channel>=0)return false;
#if CONFIG_IRRIGATION_SIMULATION
  channel=static_cast<int>(index);return true;
#else
  if(!cycle_on(index))return false;
  channel=static_cast<int>(index);return true;
#endif
 }
} pump;
static nvs_handle_t journal_handle=0,event_handle=0;
static const JournalState& load_journal(){
 static JournalState state;
 if(nvs_flash_init_partition("journal")!=ESP_OK||nvs_open_from_partition("journal","commands",NVS_READWRITE,&journal_handle)!=ESP_OK){state.version=0;return state;}
 size_t length=sizeof(state);auto result=nvs_get_blob(journal_handle,"state",&state,&length);
 if(result==ESP_OK){if(length<offsetof(JournalState,slots)||length>sizeof(state)||(length-offsetof(JournalState,slots))%sizeof(JournalSlot))state.version=0;return state;}
 if(result!=ESP_ERR_NVS_NOT_FOUND){state.version=0;return state;}
 // Read-only migration: preserve the original default-NVS namespace completely.
 nvs_handle_t legacy;
 result=nvs_open("commands",NVS_READONLY,&legacy);
 if(result==ESP_ERR_NVS_NOT_FOUND)return state;
 if(result!=ESP_OK){state.version=0;return state;}
 for(unsigned i=0;i<128;++i){char key[12];snprintf(key,sizeof(key),"id%u",i);size_t size=sizeof(state.slots[i].id);auto read=nvs_get_str(legacy,key,state.slots[i].id,&size);if(read!=ESP_OK&&read!=ESP_ERR_NVS_NOT_FOUND){state.version=0;break;}}
 int64_t cycle=0;result=nvs_get_i64(legacy,"cycle_at",&cycle);if(result==ESP_OK)state.cycle_at=cycle;else if(result!=ESP_ERR_NVS_NOT_FOUND)state.version=0;
 nvs_close(legacy);return state;
}
static bool save_journal(const JournalState& state){size_t used=0;for(size_t i=0;i<state.slots.size();++i)if(state.slots[i].id[0])used=i+1;size_t length=offsetof(JournalState,slots)+used*sizeof(JournalSlot);return journal_handle&&nvs_set_blob(journal_handle,"state",&state,length)==ESP_OK&&nvs_commit(journal_handle)==ESP_OK;}
struct EventCheckpoint {uint32_t version=1,count=0;char messages[32][768]={};};
static bool event_storage_valid=false;
static std::deque<std::string> load_events(){
 std::deque<std::string> result;
 if(nvs_flash_init_partition("events")!=ESP_OK||nvs_open_from_partition("events","outbox",NVS_READWRITE,&event_handle)!=ESP_OK)return result;
 static EventCheckpoint state;size_t length=sizeof(state);auto read=nvs_get_blob(event_handle,"pending",&state,&length);
 if(read==ESP_ERR_NVS_NOT_FOUND){event_storage_valid=true;return result;}
 if(read!=ESP_OK||length<offsetof(EventCheckpoint,messages)||state.version!=1||state.count>32)return result;
 size_t used=offsetof(EventCheckpoint,messages)+state.count*sizeof(state.messages[0]);
 if(length!=used&&length!=sizeof(state))return result; // Accept full legacy snapshot or compact prefix.
 for(unsigned i=0;i<state.count;++i){if(!memchr(state.messages[i],0,sizeof(state.messages[i])))return {};result.emplace_back(state.messages[i]);}
 event_storage_valid=true;return result;
}
static bool save_events(const std::deque<std::string>& pending){
 if(!event_handle||pending.size()>32)return false;
 static EventCheckpoint state;memset(state.messages,0,sizeof(state.messages));state.version=1;state.count=pending.size();
 for(unsigned i=0;i<state.count;++i){if(pending[i].size()>=sizeof(state.messages[i]))return false;memcpy(state.messages[i],pending[i].c_str(),pending[i].size()+1);}
 size_t used=offsetof(EventCheckpoint,messages)+state.count*sizeof(state.messages[0]);
 return nvs_set_blob(event_handle,"pending",&state,used)==ESP_OK&&nvs_commit(event_handle)==ESP_OK;
}
// Reservoir accounting (refill reference minus doses) survives reboots in the journal partition.
struct ReservoirState {uint32_t version=1;uint32_t known=0;double ml=0,reserve_ml=0;char refill_id[37]={};};
static ReservoirState reservoir_state;
static void load_reservoir(){
 size_t length=sizeof(reservoir_state);
 if(!journal_handle||nvs_get_blob(journal_handle,"reservoir",&reservoir_state,&length)!=ESP_OK||length!=sizeof(reservoir_state)||reservoir_state.version!=1||!memchr(reservoir_state.refill_id,0,sizeof(reservoir_state.refill_id)))reservoir_state=ReservoirState{};
}
static bool save_reservoir(std::optional<double> ml,double reserve_ml){
 reservoir_state.known=ml.has_value();reservoir_state.ml=ml.value_or(0);reservoir_state.reserve_ml=reserve_ml;
 return journal_handle&&nvs_set_blob(journal_handle,"reservoir",&reservoir_state,sizeof(reservoir_state))==ESP_OK&&nvs_commit(journal_handle)==ESP_OK;
}
// Manual dry pulses for bench checks (pump direction, wiring, INA channel). Off unless the provisioned
// key pump_test=1 is present (set only after the power circuit is reviewed). Hard limits for running
// out of water: <=2 s per pulse, 10 s rest per output, <=10 s on-time per output per 10 min, one at a time.
static constexpr int64_t manual_max_ms=2000,manual_rest_ms=10000,manual_budget_ms=10000,manual_window_ms=600000;
// Wet mode (pump submerged, flow calibration; the user confirms on the page): longer pulses, larger budget.
static constexpr int64_t wet_max_ms=10000,wet_budget_ms=120000;
static bool manual_wet=false;
static double manual_withdrawn_ml=NAN;
static int64_t manual_pulse_ms=0; // accepted pulse length (overrun guard); not changed by later requests
static bool manual_enabled=false;
static std::atomic<int> manual_output{-1};
static std::atomic<bool> manual_timer_fired{false};
static int64_t manual_started_ms=0,manual_requested_ms=0;
static std::string manual_id;
static std::array<int64_t,3> manual_last_end_ms{},manual_window_start_ms{},manual_used_ms{};
static esp_timer_handle_t manual_timer=nullptr;
static void all_outputs_low(){for(int pin:bench::output_pins)gpio_set_level(static_cast<gpio_num_t>(pin),0);}
static void manual_timeout(void*){all_outputs_low();manual_timer_fired=true;} // esp_timer task: GPIO only
// Automatic cycle state (see BenchPump).
static bool irrigation_enabled=false;
static std::array<int64_t,3> dose_ms{{0,0,0}};
static esp_timer_handle_t cycle_timer=nullptr;
static int cycle_unit=-1;static int64_t cycle_started_ms=0;static double cycle_last_on_s=NAN;
[[maybe_unused]] static void cycle_timeout(void*){all_outputs_low();}
static SharedRig* controller=nullptr;
static esp_mqtt_client_handle_t mqtt=nullptr;
static std::string device,broker,username,password,ca,ssid,wifi_password,ntp,experiment;
static std::array<UnitIdentity,3> units={UnitIdentity{"","T1"},UnitIdentity{"","T2"},UnitIdentity{"","T3"}};
struct Message {std::string topic,payload;};
// ~40 min of physical telemetry (3 units/min) survives a broker/Wi-Fi outage in RAM.
static Buffer<Message> outbox(120);
// 10/11: 120 telemetry messages (~1 KB each) exhausted the heap during a 6 min broker outage; Wi-Fi
// (WPA handshake) and TLS then failed until power-cycled. Bound the RAM buffer by bytes and keep a
// heap reserve for reconnection; beyond that, new telemetry is dropped and counted.
static constexpr size_t outbox_budget_b=24*1024,heap_reserve_b=48*1024;
static size_t outbox_bytes=0;
static size_t message_bytes(const Message&m){return m.topic.size()+m.payload.size()+64;}
static void enqueue(Message m){
 size_t n=message_bytes(m);
 if(outbox_bytes+n>outbox_budget_b||esp_get_free_heap_size()<heap_reserve_b+n){++outbox.dropped;return;}
 outbox.push(std::move(m));outbox_bytes+=n;
}
static void dequeue(){if(auto f=outbox.front()){outbox_bytes-=std::min(outbox_bytes,message_bytes(*f));outbox.acknowledged();}}
static DurableBuffer* durable_events=nullptr;
static std::array<Sensors,3> sensors; // Invalid by default; no fabricated physical measurements.
// Physical acquisition settings from `provision`; absent calibration => null values, never defaults.
static constexpr const char* firmware_version="physical-irrigation-1";
static constexpr double mass_stable_span_g=5; // Provisional: ~1 g noise observed on 04/11.
static ScaleCalibration scale_calibration;
static std::array<RelativeSoilCalibration,3> soil_calibration;
static std::array<int,3> ina_channel{{-1,-1,-1}}; // Unit -> INA3221 channel; unknown until verified.
static std::array<int,3> unit_output{{-1,-1,-1}};  // Unit -> pump output (0..2 = GPIO25/26/27).
static int64_t telemetry_period_s=60;
static double dose_volume_ml=NAN,soil_low=NAN,soil_high=NAN,unit_interval_s=NAN;
[[maybe_unused]] static double shared_removed_ml=0;
static bool connected=false;
static bool subscribed=false;
// Remote mode needs the decision service alive, not only the broker: without a heartbeat
// for heartbeat_timeout_ms the device falls back to local hysteresis (pre-project contingency).
static constexpr int64_t heartbeat_timeout_ms=90000;
static int64_t heartbeat_ms=-1;
static bool backend_online=false;
static std::atomic<bool> utc_synced{false};
static void clock_synced(timeval*){utc_synced.store(true);}
static int in_flight=-1;
// Delivery health (07-10/11 outages): one unconfirmed message used to block every later publish.
static constexpr int64_t in_flight_timeout_ms=60000;      // resend (ids make duplicates harmless)
static constexpr int64_t delivery_watchdog_ms=20*60*1000;  // no PUBACK this long => clean restart
static int64_t in_flight_since_ms=0,last_delivery_ms=0;
static uint32_t mqtt_connects=0,wifi_disconnects=0,in_flight_timeouts=0;
static uint8_t last_wifi_reason=0;
// 09/11: the board closed the session and then never reached the broker again while Wi-Fi stayed
// "associated" (no disconnect event). Re-associate (new DHCP/ARP) after 5 min without the broker.
static constexpr int64_t wifi_kick_ms=5*60*1000;
static int64_t mqtt_down_since_ms=0,last_wifi_kick_ms=0;
static uint32_t wifi_kicks=0;
static bool in_flight_event=false;
static int early_ack=-1;
static int early_deleted=-1;
static bool persist_event(size_t,const Event&);
static SemaphoreHandle_t mutex;
static std::string uuid(){
 uint8_t b[16];esp_fill_random(b,sizeof(b));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;char s[37];
 snprintf(s,sizeof(s),"%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);return s;
}
static std::string get(nvs_handle_t h,const char*key){size_t n=0;if(nvs_get_str(h,key,nullptr,&n)!=ESP_OK||!n)return {};std::string s(n,'\0');if(nvs_get_str(h,key,s.data(),&n)!=ESP_OK)return {};s.resize(n-1);return s;}
static int64_t epoch(){return static_cast<int64_t>(time(nullptr));}
static std::string event_stamp(){
 timeval tv;gettimeofday(&tv,nullptr);tm utc;gmtime_r(&tv.tv_sec,&utc);char base[24],stamp[40];
 strftime(base,sizeof(base),"%Y-%m-%dT%H:%M:%S",&utc);
 snprintf(stamp,sizeof(stamp),"%s.%06ldZ",base,static_cast<long>(tv.tv_usec));return stamp;
}
static const char* source(){
#if CONFIG_IRRIGATION_SIMULATION
 return "simulation";
#else
 return "physical";
#endif
}
static double parse_number(const std::string& text){
 if(text.empty())return NAN;
 char* end=nullptr;double value=strtod(text.c_str(),&end);
 return end&&*end==0?value:NAN;
}
static void add_optional(cJSON* j,const char* key,std::optional<double> value){
 if(value&&std::isfinite(*value))cJSON_AddNumberToObject(j,key,*value);else cJSON_AddNullToObject(j,key);
}
static void queue_physical_telemetry(const hardware::Snapshot& h,int64_t now){
#if !CONFIG_IRRIGATION_SIMULATION
 char stamp[32];time_t when=now;tm utc;gmtime_r(&when,&utc);strftime(stamp,sizeof(stamp),"%Y-%m-%dT%H:%M:%SZ",&utc);
 bool soil_fresh=h.soil_at_ms>=0&&h.now_ms-h.soil_at_ms<=3000;
 bool power_fresh=h.ina_ok&&h.power_at_ms>=0&&h.now_ms-h.power_at_ms<=3000;
 bool climate_fresh=h.climate&&h.climate_at_ms>=0&&h.now_ms-h.climate_at_ms<=10000;
 auto mass=h.scale_fresh?scale_calibration.grams(h.scale_counts):std::nullopt;
 for(size_t i=0;i<3;++i){
  const auto& identity=units[i];auto j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"schema_version",1);
  auto id=uuid();cJSON_AddStringToObject(j,"message_id",id.c_str());
  cJSON_AddStringToObject(j,"device_id",device.c_str());cJSON_AddStringToObject(j,"experiment_id",experiment.c_str());cJSON_AddStringToObject(j,"unit_id",identity.unit_id.c_str());
  cJSON_AddStringToObject(j,"treatment",identity.treatment.c_str());cJSON_AddStringToObject(j,"source","physical");cJSON_AddStringToObject(j,"collected_at",stamp);
  cJSON_AddStringToObject(j,"firmware_version",firmware_version);
  int raw=soil_fresh?h.soil_raw[i]:-1;
  auto index=raw>=0?soil_calibration[i].index(raw):std::nullopt;
  cJSON_AddStringToObject(j,"calibration_id",soil_calibration[i].id.empty()?"soil-uncalibrated":soil_calibration[i].id.c_str());
  if(raw>=0)cJSON_AddNumberToObject(j,"soil_raw",raw);else cJSON_AddNullToObject(j,"soil_raw");
  add_optional(j,"soil_index",index);cJSON_AddBoolToObject(j,"sensor_valid",index.has_value());
  add_optional(j,"temperature_c",climate_fresh?std::optional<double>(h.climate->temperature_c):std::nullopt);
  add_optional(j,"humidity_pct",climate_fresh?std::optional<double>(h.climate->humidity_pct):std::nullopt);
  cJSON_AddNumberToObject(j,"dht_faults",h.climate_faults);cJSON_AddNumberToObject(j,"dht_streak",h.climate_streak);cJSON_AddNumberToObject(j,"dht_resets",h.climate_resets);
  // Shared platform: the same reading is repeated per unit, not three measurements.
  add_optional(j,"reservoir_mass_g",mass);
  if(h.scale_fresh)cJSON_AddNumberToObject(j,"scale_counts",h.scale_counts);else cJSON_AddNullToObject(j,"scale_counts");
  if(mass){double span_g=std::fabs(h.scale_span/scale_calibration.counts_per_g);cJSON_AddNumberToObject(j,"scale_span_g",span_g);cJSON_AddBoolToObject(j,"mass_stable",span_g<=mass_stable_span_g);}
  else{cJSON_AddNullToObject(j,"scale_span_g");cJSON_AddNullToObject(j,"mass_stable");}
  if(scale_calibration.valid())cJSON_AddStringToObject(j,"scale_calibration_id",scale_calibration.id.c_str());
  // Shared reservoir estimate (accounting, not weighing); null until a refill is registered.
  add_optional(j,"reservoir_ml",controller?controller->reservoir():std::nullopt);
  // Link/heap diagnostics for the 07-10/11 outages (same values repeated per unit).
  cJSON_AddNumberToObject(j,"uptime_s",static_cast<double>(esp_timer_get_time()/1000000));
  cJSON_AddNumberToObject(j,"reset_reason",static_cast<int>(esp_reset_reason()));
  cJSON_AddNumberToObject(j,"heap_free_b",static_cast<double>(esp_get_free_heap_size()));
  cJSON_AddNumberToObject(j,"heap_min_b",static_cast<double>(esp_get_minimum_free_heap_size()));
  cJSON_AddNumberToObject(j,"heap_largest_b",static_cast<double>(heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)));
  {wifi_ap_record_t ap;if(esp_wifi_sta_get_ap_info(&ap)==ESP_OK)cJSON_AddNumberToObject(j,"wifi_rssi_dbm",ap.rssi);else cJSON_AddNullToObject(j,"wifi_rssi_dbm");}
  cJSON_AddNumberToObject(j,"mqtt_connects",mqtt_connects);cJSON_AddNumberToObject(j,"wifi_disconnects",wifi_disconnects);
  cJSON_AddNumberToObject(j,"wifi_last_reason",last_wifi_reason);cJSON_AddNumberToObject(j,"publish_timeouts",in_flight_timeouts);cJSON_AddNumberToObject(j,"wifi_kicks",wifi_kicks);cJSON_AddNumberToObject(j,"outbox_dropped",static_cast<double>(outbox.dropped));cJSON_AddBoolToObject(j,"manual_enabled",manual_enabled);
  if(controller&&controller->reservoir()){cJSON_AddNumberToObject(j,"reservoir_reserve_ml",controller->reserve());cJSON_AddStringToObject(j,"reservoir_ref",reservoir_state.refill_id);}
  int channel=ina_channel[i];
  bool power=channel>=0&&power_fresh&&h.power[channel].has_value();
  add_optional(j,"pump_voltage_v",power?std::optional<double>(h.power[channel]->bus_v):std::nullopt);
  add_optional(j,"pump_current_a",power?std::optional<double>(h.power[channel]->current_a):std::nullopt);
  if(channel>=0)cJSON_AddNumberToObject(j,"ina_channel",channel+1);
  if(unit_output[i]>=0)cJSON_AddNumberToObject(j,"pump_output",unit_output[i]+1);
  char*s=cJSON_PrintUnformatted(j);if(s){enqueue({"irrigation/v1/"+device+"/telemetry",s});free(s);}cJSON_Delete(j);
 }
#else
 (void)h;(void)now;
#endif
}
static const char* str(cJSON*j,const char*k){auto v=cJSON_GetObjectItemCaseSensitive(j,k);return cJSON_IsString(v)?v->valuestring:nullptr;}
static double num(cJSON*j,const char*k){auto v=cJSON_GetObjectItemCaseSensitive(j,k);return cJSON_IsNumber(v)?v->valuedouble:NAN;}
static bool valid_uuid(const char*s){
 if(!s||strlen(s)!=36)return false;
 for(size_t i=0;i<36;++i){bool separator=i==8||i==13||i==18||i==23;if(separator?s[i]!='-':!std::isxdigit(static_cast<unsigned char>(s[i])))return false;}
 return true;
}
static int64_t iso(const char*s){
 if(!s||strlen(s)<20||strlen(s)>32)return 0;
 int y,m,d,h,min;double sec;char zone[8]={};
 if(sscanf(s,"%4d-%2d-%2dT%2d:%2d:%lf%7s",&y,&m,&d,&h,&min,&sec,zone)!=7||(strcmp(zone,"Z")&&strcmp(zone,"+00:00")))return 0;
 if(y<2020||y>2100||m<1||m>12||d<1||d>31||h<0||h>23||min<0||min>59||!std::isfinite(sec)||sec<0||sec>=60)return 0;
 tm t={};t.tm_year=y-1900;t.tm_mon=m-1;t.tm_mday=d;t.tm_hour=h;t.tm_min=min;t.tm_sec=static_cast<int>(sec);
 auto result=mktime(&t);return t.tm_year==y-1900&&t.tm_mon==m-1&&t.tm_mday==d?result:0;
}
static void acknowledge_message(){
 last_delivery_ms=esp_timer_get_time()/1000;
 if(in_flight_event){if(!durable_events->acknowledged())controller->watchdog(epoch());}
 else dequeue();
 in_flight=-1;
}
static void manual_result(const std::string& id,int output,const char* status,const char* reason,int64_t on_ms,const hardware::PowerPeaks* peaks){
 auto j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"schema_version",1);cJSON_AddStringToObject(j,"test_id",id.c_str());
 cJSON_AddStringToObject(j,"device_id",device.c_str());cJSON_AddNumberToObject(j,"output",output);
 cJSON_AddStringToObject(j,"status",status);cJSON_AddStringToObject(j,"reason",reason);
 cJSON_AddNumberToObject(j,"requested_ms",static_cast<double>(manual_requested_ms));cJSON_AddNumberToObject(j,"on_ms",static_cast<double>(on_ms));
 cJSON_AddStringToObject(j,"at",event_stamp().c_str());
 if(output>=1&&output<=3){int64_t now=esp_timer_get_time()/1000;size_t i=output-1;
  int64_t used=now-manual_window_start_ms[i]>manual_window_ms?0:manual_used_ms[i];cJSON_AddNumberToObject(j,"budget_left_ms",static_cast<double>(std::max<int64_t>(0,(manual_wet?wet_budget_ms:manual_budget_ms)-used)));}
 cJSON_AddStringToObject(j,"mode",manual_wet?"wet":"dry");
 if(peaks){add_optional(j,"withdrawn_ml",std::isfinite(manual_withdrawn_ml)?std::optional<double>(manual_withdrawn_ml):std::nullopt);
  add_optional(j,"reservoir_ml",controller?controller->reservoir():std::nullopt);}
 if(peaks){auto a=cJSON_AddArrayToObject(j,"ina");cJSON_AddNumberToObject(j,"ina_samples",peaks->samples);
  for(size_t ch=0;ch<3;++ch){auto c=cJSON_CreateObject();cJSON_AddNumberToObject(c,"channel",ch+1);
   add_optional(c,"peak_current_a",std::isfinite(peaks->peak_current_a[ch])?std::optional<double>(peaks->peak_current_a[ch]):std::nullopt);
   add_optional(c,"min_bus_v",std::isfinite(peaks->min_bus_v[ch])?std::optional<double>(peaks->min_bus_v[ch]):std::nullopt);cJSON_AddItemToArray(a,c);}}
 char*s=cJSON_PrintUnformatted(j);if(s){enqueue({"irrigation/v1/"+device+"/manual_result",s});free(s);}cJSON_Delete(j);
}
static void manual_finish(const char* status,const char* reason){
 all_outputs_low();if(manual_timer)esp_timer_stop(manual_timer);hardware::power_fast(false);hardware::pumps_active(false);
 int output=manual_output.exchange(-1);if(output<0)return;
 int64_t now=esp_timer_get_time()/1000,on=now-manual_started_ms;size_t i=output;
 manual_last_end_ms[i]=now;manual_used_ms[i]+=std::min(on,(manual_wet?wet_max_ms:manual_max_ms)+500);
 // 16/11: pumps stay submerged, so a manual pulse also leaves the reservoir. Estimate it from the
 // pot's calibrated flow (dose_ml / dose time) and the GPIO time (the timer cuts at the request).
 manual_withdrawn_ml=NAN;
 for(size_t u=0;u<3;++u)if(unit_output[u]==output&&dose_ms[u]>0&&std::isfinite(dose_volume_ml)&&controller){
  double ml=std::min(on,manual_pulse_ms)*dose_volume_ml/dose_ms[u];if(controller->withdraw(ml))manual_withdrawn_ml=ml;}
 auto peaks=hardware::take_peaks();manual_result(manual_id,output+1,status,reason,on,&peaks);
 ESP_LOGI(TAG,"Manual pulse output %d ended after %lld ms (%s)",output+1,static_cast<long long>(on),reason);
}
static void manual_request(cJSON* j){
 auto id=str(j,"test_id"),dev=str(j,"device_id");double output_n=num(j,"output"),duration=num(j,"duration_ms");
 int64_t issued=iso(str(j,"issued_at")),expires=iso(str(j,"expires_at")),now_s=epoch();
 if(num(j,"schema_version")!=1||!valid_uuid(id)||!dev||device!=dev)return;
 std::string test=id;int output=std::isfinite(output_n)?static_cast<int>(output_n):-1;
 manual_requested_ms=std::isfinite(duration)?static_cast<int64_t>(duration):0;
 auto mode=str(j,"mode");bool wet=mode&&!strcmp(mode,"wet");
 if(manual_output.load()<0)manual_wet=wet; // result labels follow the request unless a pulse is running
 int64_t max_ms=wet?wet_max_ms:manual_max_ms,budget_ms=wet?wet_budget_ms:manual_budget_ms;
 if(output==0){manual_finish("stopped","stop_requested");manual_result(test,0,"stopped","all_outputs_low",0,nullptr);return;}
 const char* reason=nullptr;int64_t now=esp_timer_get_time()/1000;size_t i=output>=1&&output<=3?output-1:0;
 if(output<1||output>3)reason="invalid_output";
 else if(!issued||expires<=issued||expires-issued>60||now_s<issued-5||now_s>=expires)reason="expired_or_future";
 else if(!manual_enabled)reason="manual_disabled_until_circuit_review";
 else if(!std::isfinite(duration)||duration<100||duration>max_ms)reason="duration_out_of_limits";
 else if(controller->running())reason="irrigation_cycle_running";
 else if(manual_output.load()>=0)reason="another_output_running";
 else if(manual_last_end_ms[i]&&now-manual_last_end_ms[i]<manual_rest_ms)reason="rest_between_pulses";
 else{if(now-manual_window_start_ms[i]>manual_window_ms){manual_window_start_ms[i]=now;manual_used_ms[i]=0;}
  if(manual_used_ms[i]+static_cast<int64_t>(duration)>budget_ms)reason=wet?"wet_budget_120s_per_10min":"dry_run_budget_10s_per_10min";}
 if(reason){manual_result(test,output,"rejected",reason,0,nullptr);ESP_LOGW(TAG,"Manual pulse rejected: %s",reason);return;}
 hardware::take_peaks();hardware::power_fast(true);hardware::pumps_active(true);
 manual_id=test;manual_started_ms=now;manual_timer_fired=false;manual_pulse_ms=static_cast<int64_t>(duration);manual_wet=wet;manual_output=output-1;
 if(!manual_timer||esp_timer_start_once(manual_timer,static_cast<uint64_t>(duration)*1000)!=ESP_OK){manual_finish("failed","timer_unavailable");return;}
 gpio_set_level(static_cast<gpio_num_t>(bench::output_pins[i]),1);
 ESP_LOGW(TAG,"Manual pulse: output %d (GPIO%d) for %d ms",output,bench::output_pins[i],static_cast<int>(duration));
}
static bool cycle_on(size_t unit){
#if CONFIG_IRRIGATION_SIMULATION
 (void)unit;return false;
#else
 if(!irrigation_enabled||unit>=3||unit_output[unit]<0||dose_ms[unit]<=0||!cycle_timer||manual_output.load()>=0||cycle_unit>=0)return false;
 all_outputs_low();hardware::take_peaks();hardware::power_fast(true);hardware::pumps_active(true);
 if(esp_timer_start_once(cycle_timer,static_cast<uint64_t>(dose_ms[unit])*1000)!=ESP_OK){hardware::power_fast(false);hardware::pumps_active(false);return false;}
 cycle_unit=static_cast<int>(unit);cycle_started_ms=esp_timer_get_time()/1000;cycle_last_on_s=NAN;
 gpio_set_level(static_cast<gpio_num_t>(bench::output_pins[unit_output[unit]]),1);
 ESP_LOGW(TAG,"Cycle: %s output %d (GPIO%d) for %lld ms",units[unit].unit_id.c_str(),unit_output[unit]+1,bench::output_pins[unit_output[unit]],static_cast<long long>(dose_ms[unit]));
 return true;
#endif
}
static void cycle_off(){
#if !CONFIG_IRRIGATION_SIMULATION
 if(cycle_unit<0)return; // never touch a manual pulse's output
 all_outputs_low();if(cycle_timer)esp_timer_stop(cycle_timer);
 int64_t on=esp_timer_get_time()/1000-cycle_started_ms;
 // The timer already cut the GPIO at the dose time; the controller's off arrives up to 1 s later.
 cycle_last_on_s=std::min(on,dose_ms[cycle_unit])/1000.0;
 hardware::power_fast(false);hardware::pumps_active(false);auto peaks=hardware::take_peaks();
 int ch=ina_channel[cycle_unit];
 ESP_LOGI(TAG,"Cycle %s off: %.2f s, peak %.3f A",units[cycle_unit].unit_id.c_str(),cycle_last_on_s,ch>=0?peaks.peak_current_a[ch]:NAN);
 cycle_unit=-1;
#endif
}
static void mqtt_event(void*,esp_event_base_t,int32_t id,void*data){
 xSemaphoreTake(mutex,portMAX_DELAY);
 auto e=static_cast<esp_mqtt_event_handle_t>(data);
 if(id==MQTT_EVENT_CONNECTED){connected=true;++mqtt_connects;esp_mqtt_client_subscribe(mqtt,("irrigation/v1/"+device+"/commands").c_str(),1);esp_mqtt_client_subscribe(mqtt,("irrigation/v1/"+device+"/heartbeat").c_str(),0);esp_mqtt_client_subscribe(mqtt,("irrigation/v1/"+device+"/reservoir").c_str(),1);esp_mqtt_client_subscribe(mqtt,("irrigation/v1/"+device+"/manual").c_str(),1);
  // Main loop opens the remote barrier only after subscription/clock/backlog checks.
 }else if(id==MQTT_EVENT_DISCONNECTED){connected=false;subscribed=false;backend_online=false;heartbeat_ms=-1;controller->connectivity(false);
  // The library may drop the unconfirmed message while offline; resend it after reconnecting.
  if(in_flight>=0)in_flight=-1;}
 else if(id==MQTT_EVENT_SUBSCRIBED){subscribed=true;}
 else if(id==MQTT_EVENT_PUBLISHED){if(in_flight==-2)early_ack=e->msg_id;else if(e->msg_id==in_flight)acknowledge_message();}
 else if(id==MQTT_EVENT_DELETED){if(in_flight==-2)early_deleted=e->msg_id;else if(e->msg_id==in_flight)in_flight=-1;}
 else if(id==MQTT_EVENT_DATA){
  if(e->total_data_len!=e->data_len||e->current_data_offset||e->data_len>2048){xSemaphoreGive(mutex);return;}
  std::string topic(e->topic,e->topic_len);
  // Arrival time only (monotonic clock); payload content is not trusted for liveness.
  if(topic=="irrigation/v1/"+device+"/heartbeat"){heartbeat_ms=esp_timer_get_time()/1000;xSemaphoreGive(mutex);return;}
  if(topic=="irrigation/v1/"+device+"/manual"){
#if !CONFIG_IRRIGATION_SIMULATION
   auto j=cJSON_ParseWithLength(e->data,e->data_len);if(j){manual_request(j);cJSON_Delete(j);}
#endif
   xSemaphoreGive(mutex);return;
  }
  if(topic=="irrigation/v1/"+device+"/reservoir"){
   // Refill registration: fresh (<=5 min), for this device/experiment, once per refill_id, pumps off.
   auto j=cJSON_ParseWithLength(e->data,e->data_len);
   if(j){
    auto rid=str(j,"refill_id"),dev=str(j,"device_id"),exp=str(j,"experiment_id");
    int64_t issued=iso(str(j,"issued_at")),expires=iso(str(j,"expires_at")),now=epoch();double total=num(j,"total_ml"),reserve=num(j,"reserve_ml");
    bool ok=num(j,"schema_version")==1&&valid_uuid(rid)&&dev&&exp&&device==dev&&experiment==exp&&issued&&expires>issued&&expires-issued<=300&&now>=issued-5&&now<expires
            &&std::isfinite(total)&&total>=0&&total<=5000&&std::isfinite(reserve)&&reserve>=0&&reserve<=total&&strcmp(rid,reservoir_state.refill_id);
    if(ok){char previous[37];memcpy(previous,reservoir_state.refill_id,sizeof(previous));strncpy(reservoir_state.refill_id,rid,36);reservoir_state.refill_id[36]=0;
     if(!controller->set_reservoir(total,reserve)){memcpy(reservoir_state.refill_id,previous,sizeof(previous));ESP_LOGW(TAG,"Refill rejected (cycle running or storage)");}
     else ESP_LOGI(TAG,"Refill registered: %.0f mL, reserve %.0f mL",total,reserve);}
    else ESP_LOGW(TAG,"Refill message rejected");
    cJSON_Delete(j);
   }
   xSemaphoreGive(mutex);return;
  }
  if(topic!="irrigation/v1/"+device+"/commands"){xSemaphoreGive(mutex);return;}
  auto j=cJSON_ParseWithLength(e->data,e->data_len);if(!j){xSemaphoreGive(mutex);return;}
  auto cid=str(j,"command_id"),dev=str(j,"device_id"),source=str(j,"source");
  auto experiment=str(j,"experiment_id"),unit=str(j,"unit_id"),treatment=str(j,"treatment"),reason=str(j,"reason");
  if(valid_uuid(cid)&&dev&&source&&experiment&&unit&&treatment&&reason&&device==dev&&::experiment==experiment&&num(j,"schema_version")==1&&!strcmp(source,::source())){
   Command c{cid,iso(str(j,"issued_at")),iso(str(j,"expires_at")),num(j,"target_volume_ml"),num(j,"max_duration_s")};
   for(size_t i=0;i<3;++i)if(units[i].unit_id==unit){
    if(units[i].treatment!=treatment)persist_event(i,{cid,"rejected","unit_treatment_mismatch",false});
    else if(c.expires_at-c.issued_at<=300&&c.duration_s<=300)controller->submit(i,c,sensors[i],epoch());
    break;
   }
  }
  cJSON_Delete(j);
 }
 xSemaphoreGive(mutex);
}
static void wifi_event(void*,esp_event_base_t base,int32_t id,void*data){
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_START)esp_wifi_connect();
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_DISCONNECTED){++wifi_disconnects;last_wifi_reason=static_cast<wifi_event_sta_disconnected_t*>(data)->reason;ESP_LOGW(TAG,"Wi-Fi disconnected (reason %u)",last_wifi_reason);esp_wifi_connect();}
 if(base==IP_EVENT&&id==IP_EVENT_STA_GOT_IP&&!ntp.empty())esp_netif_sntp_start();
}
static bool persist_event(size_t channel,const Event&e){
  if(!durable_events)return false;
  auto j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"schema_version",1);
  auto eid=uuid();cJSON_AddStringToObject(j,"event_id",eid.c_str());
  cJSON_AddStringToObject(j,"command_id",e.command_id.c_str());cJSON_AddStringToObject(j,"device_id",device.c_str());
  cJSON_AddStringToObject(j,"source",source());auto stamp=event_stamp();
  cJSON_AddStringToObject(j,"origin",e.local?"local":"remote");
  cJSON_AddStringToObject(j,"experiment_id",experiment.c_str());cJSON_AddStringToObject(j,"unit_id",units[channel].unit_id.c_str());cJSON_AddStringToObject(j,"treatment",units[channel].treatment.c_str());
  // Water = scale difference around this dose only; pump energy not integrated yet (null).
  cJSON_AddBoolToObject(j,"measurements_valid",e.withdrawn_ml.has_value());
  if(e.withdrawn_ml)cJSON_AddNumberToObject(j,"volume_ml",*e.withdrawn_ml);
  if(e.duration_s){double dosed=*e.duration_s;
#if !CONFIG_IRRIGATION_SIMULATION
   if(std::isfinite(cycle_last_on_s)){dosed=cycle_last_on_s;cycle_last_on_s=NAN;} // pump on-time, not whole seconds
#endif
   cJSON_AddNumberToObject(j,"duration_s",dosed);}
  cJSON_AddStringToObject(j,"occurred_at",stamp.c_str());cJSON_AddStringToObject(j,"status",e.status.c_str());cJSON_AddStringToObject(j,"reason",e.reason.c_str());
  char*s=cJSON_PrintUnformatted(j);bool saved=s&&durable_events->push_cycle(s,e.status);if(s)free(s);cJSON_Delete(j);
  return saved;
}
[[maybe_unused]] static void queue_synthetic_telemetry(size_t channel){
#if CONFIG_IRRIGATION_SIMULATION
 const auto& sensor=sensors[channel];const auto& identity=units[channel];
 auto j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"schema_version",1);
 auto id=uuid();cJSON_AddStringToObject(j,"message_id",id.c_str());
 cJSON_AddStringToObject(j,"device_id",device.c_str());cJSON_AddStringToObject(j,"experiment_id",experiment.c_str());cJSON_AddStringToObject(j,"unit_id",identity.unit_id.c_str());
 cJSON_AddStringToObject(j,"treatment",identity.treatment.c_str());cJSON_AddStringToObject(j,"source","simulation");
 char stamp[32];time_t now=time(nullptr);tm utc;gmtime_r(&now,&utc);strftime(stamp,sizeof(stamp),"%Y-%m-%dT%H:%M:%SZ",&utc);cJSON_AddStringToObject(j,"collected_at",stamp);
 auto calibration="sim-fixture-"+identity.unit_id;cJSON_AddStringToObject(j,"calibration_id",calibration.c_str());cJSON_AddNumberToObject(j,"soil_raw",2000);cJSON_AddNumberToObject(j,"soil_index",sensor.soil);cJSON_AddBoolToObject(j,"sensor_valid",sensor.valid);
 cJSON_AddNumberToObject(j,"temperature_c",23);cJSON_AddNumberToObject(j,"humidity_pct",50);cJSON_AddNumberToObject(j,"reservoir_mass_g",sensor.available_ml);
 char*s=cJSON_PrintUnformatted(j);if(s){enqueue({"irrigation/v1/"+device+"/telemetry",s});free(s);}cJSON_Delete(j);
#endif
 (void)channel;
}
extern "C" void app_main(){
 pump.off_all(); // First application action; physical driver intentionally absent.
#if !CONFIG_IRRIGATION_SIMULATION
 hardware::outputs_low();
#endif
 setenv("TZ","UTC0",1);tzset();
 if(nvs_flash_init()!=ESP_OK){ESP_LOGE(TAG,"NVS unavailable; locked off");return;}
 static RetainedJournal journal(load_journal(),save_journal);
 auto restored=load_events();static DurableBuffer event_buffer(32,std::move(restored),save_events,event_storage_valid);durable_events=&event_buffer;
 mutex=xSemaphoreCreateMutex();
 nvs_handle_t settings;
 if(nvs_flash_init_partition("provision")==ESP_OK&&nvs_open_from_partition("provision","settings",NVS_READONLY,&settings)==ESP_OK){
  device=get(settings,"device");broker=get(settings,"broker");username=get(settings,"username");password=get(settings,"password");ca=get(settings,"ca");ssid=get(settings,"ssid");wifi_password=get(settings,"wifi_password");ntp=get(settings,"ntp");experiment=get(settings,"experiment");
  units[0].unit_id=get(settings,"unit1");if(units[0].unit_id.empty())units[0].unit_id=get(settings,"unit");units[1].unit_id=get(settings,"unit2");units[2].unit_id=get(settings,"unit3");
  scale_calibration={get(settings,"scale_cal"),parse_number(get(settings,"scale_offset")),parse_number(get(settings,"scale_factor"))};
  auto soil_id=get(settings,"soil_cal");
  for(size_t i=0;i<3;++i){
   auto suffix=std::to_string(i+1);
   soil_calibration[i]={soil_id,parse_number(get(settings,("dry"+suffix).c_str())),parse_number(get(settings,("wet"+suffix).c_str()))};
   double channel=parse_number(get(settings,("ina"+suffix).c_str()));
   if(channel==1||channel==2||channel==3)ina_channel[i]=static_cast<int>(channel)-1;
   double output=parse_number(get(settings,("out"+suffix).c_str()));
   if(output==1||output==2||output==3)unit_output[i]=static_cast<int>(output)-1;
  }
  manual_enabled=get(settings,"pump_test")=="1";
  irrigation_enabled=get(settings,"irrigation")=="1";
  for(size_t i=0;i<3;++i){double ms=parse_number(get(settings,("dose"+std::to_string(i+1)).c_str()));dose_ms[i]=std::isfinite(ms)&&ms>=300&&ms<=10000?static_cast<int64_t>(ms):0;}
  dose_volume_ml=parse_number(get(settings,"dose_ml"));soil_low=parse_number(get(settings,"soil_low"));soil_high=parse_number(get(settings,"soil_high"));
  unit_interval_s=parse_number(get(settings,"interval_s"));
  double period=parse_number(get(settings,"telemetry_s"));
  if(period>=10&&period<=900)telemetry_period_s=static_cast<int64_t>(period);
  nvs_close(settings);
 }
 if(units[0].unit_id.empty()||units[1].unit_id.empty()||units[2].unit_id.empty()||units[0].unit_id==units[1].unit_id||units[0].unit_id==units[2].unit_id||units[1].unit_id==units[2].unit_id){ESP_LOGE(TAG,"Configure three distinct units; locked off");return;}
 std::array<Limits,3> limits{};
#if !CONFIG_IRRIGATION_SIMULATION
 // Physical limits only from provisioning (protocol: 50 mL dose, 45 min per pot, <=10 s). The controller
 // duration is the dose time + 1 s so its whole-second off never cuts the timed dose short.
 {bool ok=irrigation_enabled&&std::isfinite(dose_volume_ml)&&dose_volume_ml>0&&dose_volume_ml<=80&&std::isfinite(soil_low)&&std::isfinite(soil_high)&&soil_low>=0&&soil_high<=100&&soil_low<soil_high
          &&std::isfinite(unit_interval_s)&&unit_interval_s>=600&&scale_calibration.valid();
  for(size_t i=0;i<3;++i){bool unit_ok=ok&&dose_ms[i]>0&&unit_output[i]>=0&&!soil_calibration[i].id.empty();
   limits[i]=Limits{unit_ok,dose_volume_ml,dose_ms[i]/1000.0+1,soil_low,soil_high,static_cast<int64_t>(unit_interval_s),30,0};}
  ESP_LOGW(TAG,"Automatic irrigation %s",limits[0].calibrated&&limits[1].calibrated&&limits[2].calibrated?"ENABLED":"locked (irrigation/dose/soil/scale not provisioned)");}
#endif
#if CONFIG_IRRIGATION_SIMULATION
 // Explicit synthetic fixture limits, never physical calibration.
 if(device.rfind("sim-",0)!=0||experiment.rfind("sim-",0)!=0){ESP_LOGE(TAG,"Simulation requires sim- identities");return;}
 limits.fill(Limits{true,50,10,30,60,20,5});
 static SharedRig core(units,limits,journal,pump,uuid,persist_event,20,2);controller=&core;
#else
 // Shared reservoir: 60 s between any two pumps; 15 s for the water/scale to settle before weighing.
 static SharedRig core(units,limits,journal,pump,uuid,persist_event,60,15);controller=&core;
#endif
#if CONFIG_IRRIGATION_SIMULATION
 core.set_reservoir(1000,0); // Synthetic reservoir; physical water stays unknown until a registered refill.
#else
 load_reservoir();
 core.restore_reservoir(reservoir_state.known?std::optional<double>(reservoir_state.ml):std::nullopt,reservoir_state.reserve_ml,save_reservoir);
#endif
#if !CONFIG_IRRIGATION_SIMULATION
 hardware::start();
 {esp_timer_create_args_t args={};args.callback=manual_timeout;args.name="manual_off";if(esp_timer_create(&args,&manual_timer)!=ESP_OK)manual_timer=nullptr;}
 {esp_timer_create_args_t args={};args.callback=cycle_timeout;args.name="cycle_off";if(esp_timer_create(&args,&cycle_timer)!=ESP_OK)cycle_timer=nullptr;}
#endif
 if(!device.empty()&&broker.rfind("mqtts://",0)==0&&!username.empty()&&!password.empty()&&!ca.empty()&&!ssid.empty()&&!ntp.empty()){
  ESP_ERROR_CHECK(esp_netif_init());ESP_ERROR_CHECK(esp_event_loop_create_default());esp_netif_create_default_wifi_sta();wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();ESP_ERROR_CHECK(esp_wifi_init(&init));
  esp_sntp_config_t clock=ESP_NETIF_SNTP_DEFAULT_CONFIG(ntp.c_str());clock.start=false;clock.sync_cb=clock_synced;ESP_ERROR_CHECK(esp_netif_sntp_init(&clock));
  esp_mqtt_client_config_t cfg={};cfg.broker.address.uri=broker.c_str();cfg.broker.verification.certificate=ca.c_str();cfg.credentials.username=username.c_str();cfg.credentials.authentication.password=password.c_str();cfg.credentials.client_id=device.c_str();
  // esp_mqtt_client_publish() waits on the client lock while a TLS reconnect is in progress; keep that
  // under the 15 s task watchdog (two TASK_WDT resets right after power-up on 13/11 with 5 s).
  cfg.network.timeout_ms=8000;
  mqtt=esp_mqtt_client_init(&cfg);esp_mqtt_client_register_event(mqtt,static_cast<esp_mqtt_event_id_t>(ESP_EVENT_ANY_ID),mqtt_event,nullptr);
  ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,nullptr));ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,nullptr));
  wifi_config_t w={};strncpy(reinterpret_cast<char*>(w.sta.ssid),ssid.c_str(),sizeof(w.sta.ssid)-1);strncpy(reinterpret_cast<char*>(w.sta.password),wifi_password.c_str(),sizeof(w.sta.password)-1);w.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&w));ESP_ERROR_CHECK(esp_wifi_start());
  esp_wifi_set_ps(WIFI_PS_NONE); // Mains-powered bench: no modem sleep delaying MQTT keepalive/PUBACK.
 }else ESP_LOGW(TAG,"No complete TLS/Wi-Fi credentials; offline locked-off firmware");
 esp_task_wdt_add(nullptr);
 bool mqtt_started=false;
 int64_t last_telemetry=0;
 size_t reported_dropped=0;
 for(;;){if(mqtt&&!mqtt_started&&utc_synced.load()&&epoch()>1700000000){mqtt_started=esp_mqtt_client_start(mqtt)==ESP_OK;}
  std::string publish_topic,publish_payload;
  xSemaphoreTake(mutex,portMAX_DELAY);
  {int64_t now_ms=esp_timer_get_time()/1000;
   bool alive=connected&&subscribed&&heartbeat_ms>=0&&now_ms-heartbeat_ms<=heartbeat_timeout_ms;
   if(alive!=backend_online){backend_online=alive;controller->connectivity(alive);ESP_LOGW(TAG,"Decision service %s",alive?"online":"offline: local fallback");}}
#if CONFIG_IRRIGATION_SIMULATION
  if(utc_synced.load())controller->advance(epoch());
  if(pump.channel>=0){shared_removed_ml+=1;auto&s=sensors[pump.channel];s.soil=s.soil<99?s.soil+1:100;} // Synthetic flow/response only.
  for(size_t i=0;i<3;++i){auto&s=sensors[i];if(!s.sampled_at)s.soil=40;else s.soil=s.soil>0.001?s.soil-0.001:0;s.valid=utc_synced.load();s.available_ml=shared_removed_ml<1000?1000-shared_removed_ml:0;s.removed_ml=shared_removed_ml;s.mass_g=1000-shared_removed_ml;s.sampled_at=epoch();s.mass_stable=controller->stable();}
#endif
#if !CONFIG_IRRIGATION_SIMULATION
  // Physical controller inputs: calibrated soil index and the scale's ~1 s median (only compared
  // within one cycle). Missing/uncalibrated readings stay invalid; limits remain uncalibrated, so
  // nothing actuates until the pump circuit and calibrations are approved.
  {auto h=hardware::snapshot();bool soil_fresh=h.soil_at_ms>=0&&h.now_ms-h.soil_at_ms<=3000;
   auto mass=h.scale_fresh?scale_calibration.grams(h.scale_counts):std::nullopt;
   bool stable=mass&&std::fabs(h.scale_span/scale_calibration.counts_per_g)<=mass_stable_span_g;
   for(size_t i=0;i<3;++i){auto&s=sensors[i];auto index=soil_fresh&&h.soil_raw[i]>=0?soil_calibration[i].index(h.soil_raw[i]):std::nullopt;
    s.valid=index.has_value()&&utc_synced.load();s.soil=index.value_or(0);s.removed_ml=0;s.mass_g=mass.value_or(NAN);s.mass_stable=stable;s.sampled_at=s.valid?epoch():0;}}
#endif
  if(utc_synced.load())controller->tick(sensors,epoch());
#if CONFIG_IRRIGATION_SIMULATION
  if(utc_synced.load()&&epoch()>1700000000&&epoch()-last_telemetry>=5){for(size_t i=0;i<3;++i)queue_synthetic_telemetry(i);last_telemetry=epoch();}
#else
  if(utc_synced.load()&&epoch()>1700000000&&epoch()-last_telemetry>=telemetry_period_s){queue_physical_telemetry(hardware::snapshot(),epoch());last_telemetry=epoch();}
#endif
  if(connected&&subscribed&&utc_synced.load()&&epoch()>1700000000&&outbox.size()==0&&durable_events->size()==0&&in_flight==-1&&!controller->running())controller->synchronize(epoch());
  // PUBACK only releases the delivery buffer; it never proves physical actuation.
#if !CONFIG_IRRIGATION_SIMULATION
  if(manual_output.load()>=0){int64_t now_ms=esp_timer_get_time()/1000;
   if(manual_timer_fired.load())manual_finish("done","duration_elapsed");
   else if(now_ms-manual_started_ms>manual_pulse_ms+300)manual_finish("failed","timer_overrun_forced_off");}
  if(cycle_unit>=0&&esp_timer_get_time()/1000-cycle_started_ms>dose_ms[cycle_unit]+300)all_outputs_low(); // timer backup
#endif
  {int64_t now_ms=esp_timer_get_time()/1000;
   if(connected)mqtt_down_since_ms=now_ms;
   if(mqtt_started&&!connected&&now_ms-mqtt_down_since_ms>wifi_kick_ms&&now_ms-last_wifi_kick_ms>wifi_kick_ms){
    ++wifi_kicks;last_wifi_kick_ms=now_ms;ESP_LOGW(TAG,"Broker unreachable for 5 min; re-associating Wi-Fi");esp_wifi_disconnect();} // handler reconnects
   if(in_flight>=0&&now_ms-in_flight_since_ms>in_flight_timeout_ms){++in_flight_timeouts;ESP_LOGW(TAG,"No PUBACK for message %d in %lld s; resending",in_flight,static_cast<long long>(in_flight_timeout_ms/1000));in_flight=-1;}
   // Last resort for any stall (Wi-Fi/TLS/heap): restart only with every output off and no cycle.
   if(mqtt&&now_ms-last_delivery_ms>delivery_watchdog_ms&&!controller->running()){ESP_LOGE(TAG,"No delivery for %lld min; restarting",static_cast<long long>(delivery_watchdog_ms/60000));pump.off_all();
#if !CONFIG_IRRIGATION_SIMULATION
    hardware::outputs_low();
#endif
    esp_restart();}}
  if(connected&&in_flight==-1){
   if(durable_events->front()){publish_topic="irrigation/v1/"+device+"/events";publish_payload=*durable_events->front();in_flight_event=true;}
   else if(outbox.front()){publish_topic=outbox.front()->topic;publish_payload=outbox.front()->payload;in_flight_event=false;}
   if(!publish_payload.empty()){in_flight=-2;early_ack=-1;early_deleted=-1;}
  }
  if(outbox.dropped!=reported_dropped){ESP_LOGW(TAG,"Outbound overflow: %u messages lost",static_cast<unsigned>(outbox.dropped));reported_dropped=outbox.dropped;}
  xSemaphoreGive(mutex);
  // Do not hold the controller mutex while acquiring the MQTT library API mutex.
  if(!publish_payload.empty()){
   int message=esp_mqtt_client_publish(mqtt,publish_topic.c_str(),publish_payload.c_str(),0,1,0);
   xSemaphoreTake(mutex,portMAX_DELAY);in_flight=message<0?-1:message;in_flight_since_ms=esp_timer_get_time()/1000; // <0: not sent/outbox full, retry
   if(message>=0&&early_ack==message)acknowledge_message();else if(message>=0&&early_deleted==message)in_flight=-1;
   xSemaphoreGive(mutex);
  }
  esp_task_wdt_reset();vTaskDelay(pdMS_TO_TICKS(100));}
}

#include "sensor_math.hpp"
#include <cassert>
using namespace irrigation;
static bool near(double a,double b,double tolerance=1e-9){return std::fabs(a-b)<=tolerance;}
int main(){
 // Offset binary matches HX711_ADC: zero input -> 0x800000; 8605705 is the 04/11 empty-platform offset.
 assert(hx711_offset_binary(0x000000)==0x800000&&hx711_offset_binary(0xFFFFFF)==0x7FFFFF);
 assert(hx711_saturated(0x7FFFFF)&&hx711_saturated(0x800000)&&!hx711_saturated(0x035000));
 ScaleCalibration cal{"scale-test",8605705,19.9955};
 assert(near(*cal.grams(8605705+19.9955*100),100,1e-6));
 assert(*cal.grams(8605705-200)<0); // Below tare stays negative (no clipping to zero).
 assert((!ScaleCalibration{"",1,2}.valid()&&!ScaleCalibration{"x",1,0}.valid()&&!ScaleCalibration{"x",NAN,2}.valid()));
 assert(!cal.grams(NAN));

 ScaleWindow<5> window;assert(!window.median()&&!window.stable(1000)&&window.age_ms(0)==-1);
 for(int i=0;i<4;++i)window.add(100*i,1000+i);
 assert(!window.full()&&!window.fresh(400,500));
 window.add(400,5000); // Outlier: median ignores it, stability does not.
 assert(*window.median()==1002&&!window.stable(10)&&window.stable(4000)&&*window.span()==4000);
 assert(window.fresh(800,500)&&!window.fresh(1000,500)&&!window.fresh(300,500));
 window.reset();assert(!window.full()&&window.age_ms(10)==-1);

 // INA3221: 5.32 V bus (665<<3), 2 mV shunt (50<<3) over 0.1 ohm = 20 mA.
 auto power=ina3221_decode(50<<3,665<<3,0.1);assert(power&&near(power->bus_v,5.32,1e-9)&&near(power->current_a,0.02,1e-9));
 assert(!ina3221_decode(50<<3,static_cast<uint16_t>(0xFFF8),0.1)); // Negative bus = corrupted read.
 assert(!ina3221_decode(50<<3,3300<<3,0.1)&&!ina3221_decode(50<<3,665<<3,0));
 auto reverse=ina3221_decode(static_cast<uint16_t>(0xFFB0),665<<3,0.1);assert(reverse&&reverse->current_a<0);
 assert(ina3221_identity_ok(0x5449,0x3220)&&!ina3221_identity_ok(0xFFFF,0xFFFF)&&!ina3221_identity_ok(0,0));

 // DHT22: 65.2 %RH, 35.1 C; negative and corrupted frames.
 std::array<uint8_t,5> frame{0x02,0x8C,0x01,0x5F,0};frame[4]=uint8_t(frame[0]+frame[1]+frame[2]+frame[3]);
 auto climate=dht22_decode(frame);assert(climate&&near(climate->humidity_pct,65.2)&&near(climate->temperature_c,35.1));
 frame[2]=0x80;frame[3]=0x65;frame[4]=uint8_t(frame[0]+frame[1]+frame[2]+frame[3]);assert(near(dht22_decode(frame)->temperature_c,-10.1));
 frame[4]^=1;assert(!dht22_decode(frame));
 assert(!dht22_decode({0,0,0,0,0}));
 std::array<uint8_t,5> wet{0x03,0xE9,0,200,0};wet[4]=uint8_t(wet[0]+wet[1]+wet[2]+wet[3]);assert(!dht22_decode(wet)); // 100.1 %
 return 0;
}

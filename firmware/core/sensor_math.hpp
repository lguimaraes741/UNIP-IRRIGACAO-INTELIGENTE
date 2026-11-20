#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <optional>
#include <string>

// Hardware-independent decoding/validation for the bench sensors. Drivers only
// move bits; every acceptance rule lives here so host tests can cover it.
namespace irrigation {

// HX711 24-bit two's complement, mapped to offset binary like HX711_ADC
// (raw ^ 0x800000), so offsets recorded by the Arduino diagnostic stay usable.
inline int32_t hx711_offset_binary(uint32_t raw24){return static_cast<int32_t>((raw24&0xFFFFFFu)^0x800000u);}
// Saturated codes indicate overload/disconnection, not a measurement.
inline bool hx711_saturated(uint32_t raw24){raw24&=0xFFFFFFu;return raw24==0x7FFFFFu||raw24==0x800000u;}

struct ScaleCalibration {
 std::string id;double offset_counts=NAN,counts_per_g=NAN;
 bool valid()const{return !id.empty()&&std::isfinite(offset_counts)&&std::isfinite(counts_per_g)&&std::fabs(counts_per_g)>1e-6;}
 // Sign is kept: a reading below the tare is drift/offset evidence, not zero.
 std::optional<double> grams(double counts)const{if(!valid()||!std::isfinite(counts))return {};return (counts-offset_counts)/counts_per_g;}
};

// Median of a short window (one filter layer, no dead zone or autozero).
template<size_t N> class ScaleWindow {
 std::array<int32_t,N> values{};size_t count=0,next=0;int64_t last_ms=-1;
public:
 void add(int64_t now_ms,int32_t counts){values[next]=counts;next=(next+1)%N;if(count<N)++count;last_ms=now_ms;}
 void reset(){count=next=0;last_ms=-1;}
 bool full()const{return count==N;}
 int64_t age_ms(int64_t now_ms)const{return last_ms<0?-1:now_ms-last_ms;}
 bool fresh(int64_t now_ms,int64_t max_age_ms)const{return full()&&last_ms>=0&&now_ms>=last_ms&&now_ms-last_ms<=max_age_ms;}
 std::optional<int32_t> median()const{
  if(!full())return {};
  auto copy=values;std::nth_element(copy.begin(),copy.begin()+N/2,copy.end());return copy[N/2];
 }
 std::optional<int64_t> span()const{
  if(!full())return {};
  auto [lo,hi]=std::minmax_element(values.begin(),values.end());return int64_t(*hi)-int64_t(*lo);
 }
 // Stable when the whole window spans at most `max_span` counts.
 bool stable(int32_t max_span)const{auto s=span();return s&&*s<=max_span;}
};

struct PowerReading {double bus_v,current_a;};
// INA3221: shunt LSB 40 uV, bus LSB 8 mV, both left-justified (>>3).
inline std::optional<PowerReading> ina3221_decode(uint16_t shunt_reg,uint16_t bus_reg,double shunt_ohm){
 if(!std::isfinite(shunt_ohm)||shunt_ohm<=0)return {};
 double shunt_v=(static_cast<int16_t>(shunt_reg)>>3)*40e-6;
 double bus_v=(static_cast<int16_t>(bus_reg)>>3)*8e-3;
 // A negative bus voltage is a corrupted/invalid read for this circuit (seen as -0.01 V on 04/11).
 if(bus_v<0||bus_v>26)return {};
 double current=shunt_v/shunt_ohm;
 if(!std::isfinite(current)||std::fabs(current)>1.6)return {}; // 163.8 mV full scale / 0.1 ohm.
 return PowerReading{bus_v,current};
}
inline bool ina3221_identity_ok(uint16_t manufacturer,uint16_t die){return manufacturer==0x5449&&die==0x3220;}

struct Climate {double temperature_c,humidity_pct;};
inline std::optional<Climate> dht22_decode(const std::array<uint8_t,5>& b){
 if(static_cast<uint8_t>(b[0]+b[1]+b[2]+b[3])!=b[4])return {};
 if((b[0]|b[1]|b[2]|b[3])==0)return {}; // All-zero frame passes the checksum but is a bus fault.
 double humidity=((b[0]<<8)|b[1])/10.0;
 double temperature=(((b[2]&0x7F)<<8)|b[3])/10.0;if(b[2]&0x80)temperature=-temperature;
 if(humidity>100||temperature<-40||temperature>80)return {};
 return Climate{temperature,humidity};
}
}

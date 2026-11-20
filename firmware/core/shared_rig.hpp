#pragma once
#include "controller.hpp"
#include <algorithm>
#include <array>
#include <memory>

namespace irrigation {
struct UnitIdentity {std::string unit_id,treatment;};
struct ChannelDriver {
 virtual ~ChannelDriver()=default;
 virtual void off_all()=0;
 virtual bool on(size_t channel)=0;
};
// One shared reservoir/meter domain. No GPIO mapping or pump electrical assumptions.
class SharedRig {
 struct Channel:Actuator {
  SharedRig& rig;size_t index;
  Channel(SharedRig&r,size_t i):rig(r),index(i){}
  void off()override{rig.off(index);}
  bool on()override{return rig.on(index);}
 };
 ChannelDriver& driver;Journal& journal;
 std::array<UnitIdentity,3> identities;
 std::array<std::unique_ptr<Channel>,3> channels;
 std::array<std::unique_ptr<Controller>,3> controls;
 std::function<bool(size_t,const Event&)> sink;
 int active_channel=-1;size_t next_local=0;
 // Reservoir water is accounted, not weighed: the refill reference minus each cycle's measured
 // withdrawal (or the maximum dose when it was not measured). Unknown until a refill is registered.
 // The maximum dose is deducted (and persisted) when a dose starts, so a reboot mid-dose leaves the
 // pessimistic value; a measured withdrawal then returns the difference. A failed save => unknown.
 std::optional<double> reservoir_ml;double reserve_ml=0,max_dose_ml=0;std::array<bool,3> dosing{};
 std::function<bool(std::optional<double>,double)> persist;
 void changed(){if(persist&&!persist(reservoir_ml,reserve_ml))reservoir_ml.reset();}
 void account(size_t channel,const Event&e){
  if(e.status=="started"){dosing[channel]=true;if(reservoir_ml){reservoir_ml=std::max(0.0,*reservoir_ml-max_dose_ml);changed();}return;}
  if(!dosing[channel]||(e.status!="completed"&&e.status!="failed"))return;
  dosing[channel]=false;
  if(reservoir_ml&&e.withdrawn_ml){reservoir_ml=std::max(0.0,*reservoir_ml+max_dose_ml-*e.withdrawn_ml);changed();}
 }
 double usable()const{return reservoir_ml?std::max(0.0,*reservoir_ml-reserve_ml):0;}
 int64_t now=0,last_end=0,quiet_until=0,interval_s,settle_s;
 bool initialized=false,remote=false;
 bool on(size_t channel){
  if(active_channel>=0||now<quiet_until||(last_end&&now-last_end<interval_s))return false;
  if(!driver.on(channel))return false;
  active_channel=static_cast<int>(channel);return true;
 }
 void off(size_t channel){
  if(active_channel==static_cast<int>(channel)){driver.off_all();active_channel=-1;last_end=now;quiet_until=now+settle_s;}
 }
public:
 SharedRig(std::array<UnitIdentity,3> units,Limits limits,Journal&j,ChannelDriver&d,
           std::function<std::string()> uuid,std::function<bool(size_t,const Event&)> emit,
           int64_t global_interval_s,int64_t stabilization_s)
  :SharedRig(std::move(units),std::array<Limits,3>{{limits,limits,limits}},j,d,std::move(uuid),std::move(emit),global_interval_s,stabilization_s){}
 // Per-unit limits: each pump has its own calibrated flow, so its own dose time.
 SharedRig(std::array<UnitIdentity,3> units,std::array<Limits,3> limits,Journal&j,ChannelDriver&d,
           std::function<std::string()> uuid,std::function<bool(size_t,const Event&)> emit,
           int64_t global_interval_s,int64_t stabilization_s):driver(d),journal(j),identities(std::move(units)),sink(std::move(emit)),interval_s(global_interval_s),settle_s(stabilization_s){
  driver.off_all();last_end=journal.last_cycle_at();
  for(auto&l:limits){l.settle_s=stabilization_s;max_dose_ml=std::max(max_dose_ml,l.volume_ml);}
  for(size_t i=0;i<3;++i){channels[i]=std::make_unique<Channel>(*this,i);controls[i]=std::make_unique<Controller>(limits[i],journal,*channels[i],uuid,[this,i](const Event&e){account(i,e);return sink(i,e);});}
 }
 const UnitIdentity& identity(size_t i)const{return identities.at(i);}
 int active()const{return active_channel;}
 // Refill registered with all pumps off: water now available and the reserve never dosed.
 bool set_reservoir(double available_ml,double minimum_reserve_ml){
  if(running()||!std::isfinite(available_ml)||!std::isfinite(minimum_reserve_ml)||available_ml<0||minimum_reserve_ml<0)return false;
  reservoir_ml=available_ml;reserve_ml=minimum_reserve_ml;changed();return reservoir_ml.has_value();
 }
 // Boot: restore the persisted accounting (no save); `save` is then called on every change.
 void restore_reservoir(std::optional<double> available_ml,double minimum_reserve_ml,std::function<bool(std::optional<double>,double)> save){
  if(available_ml&&std::isfinite(*available_ml)&&*available_ml>=0&&std::isfinite(minimum_reserve_ml)&&minimum_reserve_ml>=0){reservoir_ml=available_ml;reserve_ml=minimum_reserve_ml;}
  persist=std::move(save);
 }
 // Water pumped outside a cycle (manual pulses), estimated from calibrated flow; pumps off only.
 bool withdraw(double ml){
  if(running()||!reservoir_ml||!std::isfinite(ml)||ml<0)return false;
  reservoir_ml=std::max(0.0,*reservoir_ml-ml);changed();return reservoir_ml.has_value();
 }
 std::optional<double> reservoir()const{return reservoir_ml;}
 double reserve()const{return reserve_ml;}
 bool running()const{for(auto&c:controls)if(c->running())return true;return false;}
 void advance(int64_t timestamp){now=timestamp;if(!initialized){quiet_until=now+settle_s;initialized=true;}}
 bool stable()const{return initialized&&active_channel<0&&now>=quiet_until;}
 std::string blocked(const Sensors&s)const{
  if(running())return "shared_executor_busy";
  if(!stable()||!s.mass_stable)return "shared_mass_not_stable";
  if(last_end&&now-last_end<interval_s)return "global_minimum_interval";
  return {};
 }
 bool submit(size_t channel,Command command,const Sensors& sensor,int64_t timestamp){
  if(channel>=3)return false;
  advance(timestamp);auto reason=blocked(sensor);
  if(!reason.empty()){sink(channel,{command.id,"rejected",reason,false});return false;}
  Sensors accounted=sensor;accounted.available_ml=usable();
  return controls[channel]->submit(std::move(command),accounted,timestamp);
 }
 void connectivity(bool online){remote=online;for(auto&c:controls)c->connectivity(online);}
 bool synchronize(int64_t timestamp){if(running()||!stable())return false;for(auto&c:controls)if(!c->synchronize(timestamp))return false;return true;}
 void watchdog(int64_t timestamp){advance(timestamp);driver.off_all();for(auto&c:controls)c->watchdog(timestamp);active_channel=-1;quiet_until=timestamp+settle_s;}
 void tick(std::array<Sensors,3>& sensors,int64_t timestamp){
  advance(timestamp);
  for(auto&s:sensors)s.available_ml=usable();
  for(size_t i=0;i<3;++i)controls[i]->tick(sensors[i],timestamp,false);
  if(!remote&&!running()&&stable()){
   // Fair local selection: after a successful cycle, start scanning after that unit.
   for(size_t offset=0;offset<3;++offset){size_t i=(next_local+offset)%3;if(!blocked(sensors[i]).empty())continue;controls[i]->tick(sensors[i],timestamp,true);if(controls[i]->running()){next_local=(i+1)%3;break;}}
  }
 }
};
}

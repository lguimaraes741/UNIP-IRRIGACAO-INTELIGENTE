#pragma once
#include <cmath>
#include <cstdint>
#include <deque>
#include <functional>
#include <limits>
#include <optional>
#include <string>
#include <vector>
#include "measurements.hpp"

namespace irrigation {
struct Command { std::string id; int64_t issued_at, expires_at; double volume_ml, duration_s; };
// mass_g: stabilized median of the shared scale, only compared within one cycle (never absolute).
struct Sensors { bool valid=false; double soil=0, available_ml=0, removed_ml=0; int64_t sampled_at=0; bool mass_stable=true; double mass_g=std::numeric_limits<double>::quiet_NaN(); };
struct Limits { bool calibrated=false; double volume_ml=0, duration_s=0, low=0, high=0; int64_t interval_s=0, freshness_s=0, settle_s=0; };
// Persist identifiers before allowing actuation. Full storage must fail closed.
struct Journal { virtual ~Journal()=default; virtual bool observe_time(int64_t)=0; virtual bool contains(const std::string&)=0; virtual bool reserve(const std::string&,int64_t)=0; virtual int64_t last_cycle_at()=0; virtual bool record_cycle_at(int64_t)=0; };
struct Actuator { virtual ~Actuator()=default; virtual void off()=0; virtual bool on()=0; };
// Terminal events carry the dosed time and, when both stabilized masses were valid, the withdrawal.
struct Event {
 std::string command_id, status, reason; bool local=false; std::optional<double> duration_s, withdrawn_ml;
 Event(std::string id,std::string st,std::string why,bool l=false,std::optional<double> dosed={},std::optional<double> removed={})
  :command_id(std::move(id)),status(std::move(st)),reason(std::move(why)),local(l),duration_s(dosed),withdrawn_ml(removed){}
};
template<class T> class Buffer {
 std::deque<T> items; size_t capacity;
public:
 size_t dropped=0;
 explicit Buffer(size_t n):capacity(n){}
 void push(T item){if(items.size()==capacity){++dropped;return;}items.push_back(std::move(item));}
 const T* front()const{return items.empty()?nullptr:&items.front();}
 void acknowledged(){if(!items.empty())items.pop_front();}
 size_t size()const{return items.size();}
};
constexpr int64_t clock_skew_s=5;
class Controller {
 Limits limits; Journal& journal; Actuator& pump;
 std::optional<Command> active; int64_t started=0,last_end=0,dosed_s=0; double mass_before=0;
 bool settling=false;int64_t settle_until=0;
 bool remote=false,synchronized=false,armed=true;
 bool active_local=false;
 int64_t synchronized_at=0;
 std::function<std::string()> local_id;
 std::function<bool(const Event&)> durable_event;
 bool emit(Event event){if(durable_event)return durable_event(event);events.push_back(std::move(event));return true;}
 bool healthy(const Sensors&s,int64_t now)const{return limits.calibrated&&std::isfinite(limits.volume_ml)&&limits.volume_ml>0&&std::isfinite(limits.duration_s)&&limits.duration_s>0&&limits.low>=0&&limits.high<=100&&limits.low<limits.high&&limits.interval_s>=0&&limits.freshness_s>0&&s.valid&&std::isfinite(s.soil)&&s.soil>=0&&s.soil<=100&&std::isfinite(s.available_ml)&&std::isfinite(s.removed_ml)&&s.removed_ml>=0&&s.sampled_at<=now&&now-s.sampled_at<=limits.freshness_s;}
public:
 std::vector<Event> events;
 Controller(Limits l,Journal&j,Actuator&p,std::function<std::string()> id={},std::function<bool(const Event&)> sink={}):limits(l),journal(j),pump(p),local_id(std::move(id)),durable_event(std::move(sink)){pump.off();last_end=journal.last_cycle_at();}
 bool running()const{return active.has_value();}
 void connectivity(bool online){remote=online;synchronized=false;}
 bool synchronize(int64_t now=0){if(!remote||active)return false;if(!synchronized)synchronized_at=now;synchronized=true;return true;}
 bool submit(Command c,const Sensors&s,int64_t now,bool local=false){
  if(events.size()>125){stop(now,"failed","event_buffer_full");return false;}
  std::string reason;
  if(!journal.observe_time(now))reason="clock_or_journal_unavailable";
  else if(c.id.empty()||!std::isfinite(c.volume_ml)||!std::isfinite(c.duration_s))reason="invalid_command";
  else if(journal.contains(c.id))reason="duplicate";
  // Up to clock_skew_s ahead is NTP skew between server and board (~1 s seen on 06/11), not a future command.
  else if(c.issued_at>now+clock_skew_s||c.expires_at<=now||c.expires_at<=c.issued_at||c.expires_at-c.issued_at>300)reason="expired_or_future";
  else if(active)reason="busy";
  else if(!local&&(!remote||!synchronized))reason="not_synchronized";
  else if(!local&&c.issued_at<synchronized_at)reason="before_resync";
  else if(!healthy(s,now))reason="invalid_sensors_or_calibration";
  else if(!s.mass_stable)reason="mass_not_stable";
  else if(!std::isfinite(s.mass_g))reason="mass_unavailable";
  else if(c.volume_ml<=0||c.volume_ml>limits.volume_ml||c.duration_s<=0||c.duration_s>limits.duration_s)reason="limits";
  else if(s.available_ml<c.volume_ml)reason="insufficient_water";
  else if(last_end&&now-last_end<limits.interval_s)reason="minimum_interval";
  else if(!journal.reserve(c.id,c.expires_at)||!journal.record_cycle_at(now+static_cast<int64_t>(std::ceil(c.duration_s))))reason="journal_unavailable";
  if(!reason.empty()){emit({c.id,"rejected",reason,local});return false;}
  if(!emit({c.id,"accepted","validated",local})){pump.off();return false;}
  if(!pump.on()){pump.off();emit({c.id,"failed","actuator_unavailable",local});return false;}
  active=c;active_local=local;started=now;mass_before=s.mass_g;settling=false;
  if(!emit({c.id,"started","actuation",local})){stop(now,"failed","event_storage_unavailable");return false;}
  return true;
 }
 void finish(int64_t now,const Sensors&s){
  pump.off();if(!active)return;
  // Withdrawal above twice the commanded dose means a disturbance (refill, touch), not a dose.
  auto removed=cycle_withdrawal_ml(mass_before,s.mass_g,s.mass_stable,2*active->volume_ml);
  Event e{active->id,"completed",removed?"dose_time_completed":"withdrawal_not_measured",active_local,static_cast<double>(dosed_s),removed};
  if(!emit(e))limits.calibrated=false;
  active.reset();last_end=now;if(!journal.record_cycle_at(now))limits.calibrated=false;
 }
 void stop(int64_t now,const std::string&status,const std::string&reason){pump.off();if(active){if(!emit({active->id,status,reason,active_local}))limits.calibrated=false;active.reset();last_end=now;if(!journal.record_cycle_at(now))limits.calibrated=false;}}
 void watchdog(int64_t now){stop(now,"failed","watchdog");synchronized=false;}
 void tick(const Sensors&s,int64_t now,bool allow_fallback=true){
  if(active){
   if(now<started||!healthy(s,now)){stop(now,"failed","invalid_measurement");return;}
   if(settling){
    if(now>=settle_until&&s.mass_stable){finish(now,s);return;}
    if(now>settle_until+limits.settle_s+1){stop(now,"failed","mass_stabilization_timeout");return;}
    return;
   }
   // Dose by calibrated time only; the scale never stops the pump in real time.
   if(now-started>=active->duration_s){
    pump.off();dosed_s=now-started;
    if(!limits.settle_s){finish(now,s);return;}
    settling=true;settle_until=now+limits.settle_s;
    if(!journal.record_cycle_at(settle_until))stop(now,"failed","journal_unavailable");
    return;
   }
  }
  if(s.soil>=limits.high)armed=true;
  if(allow_fallback&&!remote&&!active&&armed&&healthy(s,now)&&s.soil<limits.low){
   Command c{local_id?local_id():"fallback-"+std::to_string(now),now,now+1,limits.volume_ml,limits.duration_s};
   if(submit(c,s,now,true))armed=false;
  }
 }
};
}

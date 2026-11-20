#include "shared_rig.hpp"
#include <cassert>
#include <set>
#include <tuple>
#include <algorithm>
using namespace irrigation;
struct JournalMock:Journal {
 std::set<std::string> ids;int64_t floor=0,last=0;
 bool observe_time(int64_t now)override{if(now<floor)return false;floor=now;return true;}
 bool contains(const std::string&id)override{return ids.count(id);}
 bool reserve(const std::string&id,int64_t)override{ids.insert(id);return true;}
 int64_t last_cycle_at()override{return last;}
 bool record_cycle_at(int64_t value)override{last=value;return true;}
};
struct DriverMock:ChannelDriver {
 int active=-1,starts=0;std::vector<size_t> order;
 void off_all()override{active=-1;}
 bool on(size_t channel)override{assert(active==-1);active=static_cast<int>(channel);++starts;order.push_back(channel);return true;}
};
int main(){
 JournalMock journal;DriverMock driver;std::vector<std::pair<size_t,Event>> events;
 std::array<UnitIdentity,3> units={UnitIdentity{"sim-pot-01","T1"},UnitIdentity{"sim-pot-02","T2"},UnitIdentity{"sim-pot-03","T3"}};
 Limits limits{true,50,10,30,60,20,5};int ids=0;
 auto emit=[&](size_t unit,const Event&e){events.push_back({unit,e});return true;};
 SharedRig rig(units,limits,journal,driver,[&](){return "local-"+std::to_string(++ids);},emit,20,2);
 assert(driver.active==-1&&rig.identity(2).treatment=="T3");
 assert(rig.set_reservoir(1000,200));
 std::array<Sensors,3> sensors;
 auto sample=[&](int64_t time,double removed,bool stable){for(auto&s:sensors){s={true,40,0,removed,time,stable,1000-removed};}};
 sample(100,0,false);rig.connectivity(true);rig.tick(sensors,100);assert(!rig.synchronize(100));
 assert(!rig.submit(0,{"before-stable",100,110,10,5},sensors[0],100));assert(events.back().second.reason=="shared_mass_not_stable");
 sample(102,0,true);rig.tick(sensors,102);assert(rig.synchronize(102));assert(rig.submit(0,{"unit-0",102,120,10,5},sensors[0],102));assert(driver.active==0);
 assert(!rig.submit(1,{"unit-1-busy",102,120,10,5},sensors[1],102));assert(events.back().first==1&&events.back().second.reason=="shared_executor_busy");
 // The scale never stops the pump: the dose ends at its calibrated time, then mass settles.
 sample(103,10,false);rig.tick(sensors,103);assert(driver.active==0);
 sample(107,10,false);rig.tick(sensors,107);assert(driver.active==-1&&rig.running());
 assert(!rig.synchronize(107));assert(!rig.submit(2,{"unit-2-settling",107,120,10,5},sensors[2],107));
 sample(109,10,true);rig.tick(sensors,109);assert(!rig.running());assert(events.back().first==0&&events.back().second.status=="completed");
 assert(*events.back().second.withdrawn_ml==10&&*rig.reservoir()==990);
 assert(!rig.submit(1,{"unit-1-interval",109,130,10,5},sensors[1],109));assert(events.back().second.reason=="global_minimum_interval");
 sample(130,10,true);assert(rig.submit(1,{"unit-1",130,150,10,5},sensors[1],130));assert(driver.active==1);rig.watchdog(131);assert(driver.active==-1&&!rig.running());
 assert(events.back().first==1&&events.back().second.status=="failed");
 assert(*rig.reservoir()==940); // Unmeasured cycle: the maximum dose is deducted.
 // Local requests across all units are round-robin and never overlap a remote cycle.
 rig.connectivity(false);
 for(int cycle=0;cycle<3;++cycle){
  int64_t start=160+cycle*30;double removed=10+cycle*50;sample(start,removed,true);for(auto&s:sensors)s.soil=20;
  rig.tick(sensors,start);assert(driver.active==cycle);assert(events.back().first==static_cast<size_t>(cycle)&&events.back().second.local);
  sample(start+5,removed+25,false);for(auto&s:sensors)s.soil=20;rig.tick(sensors,start+5);assert(driver.active==cycle);
  sample(start+10,removed+50,false);for(auto&s:sensors)s.soil=20;rig.tick(sensors,start+10);assert(driver.active==-1&&rig.running());
  sample(start+12,removed+50,true);for(auto&s:sensors)s.soil=20;rig.tick(sensors,start+12);assert(!rig.running());assert(events.back().first==static_cast<size_t>(cycle)&&events.back().second.status=="completed");
 }
 assert(driver.order==std::vector<size_t>({0,1,0,1,2})&&*rig.reservoir()==790);
 // Unstable post-cycle mass fails without starting another channel.
 sample(260,160,true);rig.connectivity(true);rig.advance(260);assert(rig.synchronize(260));assert(rig.submit(2,{"unstable-end",260,280,10,5},sensors[2],260));
 sample(265,170,false);rig.tick(sensors,265);sample(271,170,false);rig.tick(sensors,271);assert(events.back().second.reason=="mass_stabilization_timeout"&&driver.active==-1);
 // Reservoir accounting: unknown before refill, disturbance not counted as a dose, low water blocks.
 JournalMock j2;DriverMock d2;std::vector<std::pair<size_t,Event>> e2;
 SharedRig r2(units,limits,j2,d2,[&](){return "l2-"+std::to_string(++ids);},[&](size_t u,const Event&e){e2.push_back({u,e});return true;},20,2);
 auto at=[&](int64_t t,double mass,bool stable){for(auto&s:sensors){s={true,40,0,0,t,stable,mass};}};
 at(1000,-500,true);r2.connectivity(true);r2.tick(sensors,1000);at(1002,-500,true);r2.tick(sensors,1002);assert(r2.synchronize(1002));
 assert(!r2.submit(0,{"no-refill",1002,1020,10,5},sensors[0],1002));assert(e2.back().second.reason=="insufficient_water"&&d2.starts==0);
 assert(r2.set_reservoir(260,200));assert(r2.submit(0,{"disturbed",1002,1020,50,5},sensors[0],1002));
 at(1007,-620,false);r2.tick(sensors,1007);at(1009,-620,true);r2.tick(sensors,1009);
 assert(e2.back().second.status=="completed"&&e2.back().second.reason=="withdrawal_not_measured"&&!e2.back().second.withdrawn_ml&&*r2.reservoir()==210);
 at(1030,-620,true);r2.tick(sensors,1030);assert(!r2.submit(1,{"low",1030,1040,20,5},sensors[1],1030));assert(e2.back().second.reason=="insufficient_water");
 // Persistence: restored at boot, max dose saved at start (survives a reboot mid-dose), refund after
 // the measured withdrawal; a failed save makes the reservoir unknown (blocks dosing).
 JournalMock j3;DriverMock d3;std::vector<std::pair<std::optional<double>,double>> saved;bool storage_ok=true;
 SharedRig r3(units,limits,j3,d3,[&](){return "l3-"+std::to_string(++ids);},[&](size_t,const Event&){return true;},20,2);
 r3.restore_reservoir(700,200,[&](std::optional<double> ml,double reserve){saved.push_back({ml,reserve});return storage_ok;});
 assert(*r3.reservoir()==700&&r3.reserve()==200&&saved.empty());
 at(2000,-300,true);r3.connectivity(true);r3.tick(sensors,2000);at(2002,-300,true);r3.tick(sensors,2002);assert(r3.synchronize(2002));
 assert(r3.submit(0,{"persisted",2002,2020,40,5},sensors[0],2002));assert(*saved.back().first==650);
 at(2007,-338,false);r3.tick(sensors,2007);at(2009,-338,true);r3.tick(sensors,2009);
 assert(e2.size()&&*r3.reservoir()==662&&*saved.back().first==662);
 assert(r3.withdraw(12.5)&&*r3.reservoir()==649.5&&*saved.back().first==649.5); // manual pulse estimate
 assert(!r3.withdraw(-1)&&*r3.reservoir()==649.5);
 storage_ok=false;assert(!r3.set_reservoir(900,200));assert(!r3.reservoir());assert(!r3.withdraw(5));
 at(2040,-338,true);r3.tick(sensors,2040);assert(!r3.submit(1,{"unsaved",2040,2050,20,5},sensors[1],2040));
 // Per-unit limits (physical pumps differ in flow): each unit only accepts its own dose time;
 // an uncalibrated unit never actuates while the others still can.
 JournalMock j4;DriverMock d4;std::vector<std::pair<size_t,Event>> e4;
 std::array<Limits,3> per{{Limits{true,50,2.6,30,60,20,5},Limits{true,50,3.4,30,60,20,5},Limits{false,50,3,30,60,20,5}}};
 SharedRig r4(units,per,j4,d4,[&](){return "l4-"+std::to_string(++ids);},[&](size_t u,const Event&e){e4.push_back({u,e});return true;},20,2);
 assert(r4.set_reservoir(1000,200));at(3000,0,true);r4.connectivity(true);r4.tick(sensors,3000);at(3002,0,true);r4.tick(sensors,3002);assert(r4.synchronize(3002));
 assert(!r4.submit(0,{"too-long",3002,3020,50,3},sensors[0],3002));assert(e4.back().second.reason=="limits");
 assert(!r4.submit(2,{"uncalibrated",3002,3020,50,1},sensors[2],3002));assert(e4.back().second.reason=="invalid_sensors_or_calibration");
 assert(r4.submit(1,{"unit-1-own-time",3002,3020,50,3},sensors[1],3002)&&d4.active==1);
 // Local fallback (backend offline): the uncalibrated unit stays off.
 JournalMock j5;DriverMock d5;
 SharedRig r5(units,per,j5,d5,[&](){return "l5-"+std::to_string(++ids);},[&](size_t,const Event&){return true;},20,2);
 assert(r5.set_reservoir(1000,200));
 for(auto&s:sensors){s={true,10,0,0,4000,true,0};}r5.tick(sensors,4000);
 for(int64_t t=4002;t<4400;t+=1){for(auto&s:sensors){s={true,10,0,0,t,true,0};}r5.tick(sensors,t);assert(d5.active!=2);}
 assert(std::count(d5.order.begin(),d5.order.end(),0)&&std::count(d5.order.begin(),d5.order.end(),1)&&!std::count(d5.order.begin(),d5.order.end(),2));
}

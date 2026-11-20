#include "controller.hpp"
#include "measurements.hpp"
#include "retention.hpp"
#include "durable_buffer.hpp"
#include <cassert>
#include <set>
using namespace irrigation;
struct MemoryJournal:Journal{std::set<std::string> ids;bool fail=false;int64_t last=0,floor=0;bool observe_time(int64_t now)override{if(fail||now<floor)return false;floor=now;return true;}bool contains(const std::string&i)override{return ids.count(i);}bool reserve(const std::string&i,int64_t)override{if(fail)return false;ids.insert(i);return true;}int64_t last_cycle_at()override{return last;}bool record_cycle_at(int64_t now)override{if(fail)return false;last=now;return true;}};
struct Pump:Actuator{bool enabled=false;int starts=0;void off()override{enabled=false;}bool on()override{enabled=true;++starts;return true;}};
int main(){
 MemoryJournal j;Pump p;Limits l{true,50,10,30,60,20,5};Controller c(l,j,p);assert(!p.enabled);
 Sensors s{true,40,100,0,100,true,1000};c.connectivity(true);assert(c.synchronize());
 assert(!c.submit({"expired",80,100,10,5},s,100));assert(!c.submit({"large",100,110,51,5},s,100));
 s.valid=false;assert(!c.submit({"bad",100,110,10,5},s,100));s.valid=true;
 assert(c.submit({"one",100,110,10,5},s,100));assert(!c.submit({"one",100,110,10,5},s,100));
 s.sampled_at=105;c.tick(s,105);assert(!p.enabled);assert(c.events.back().status=="completed"&&c.events.back().reason=="dose_time_completed"&&*c.events.back().duration_s==5&&*c.events.back().withdrawn_ml==0);
 assert(!c.submit({"too_soon",105,120,10,5},s,105));
 // Mass dropping during the dose never stops the pump; only the calibrated time does.
 s.sampled_at=130;assert(c.submit({"two",130,140,10,5},s,130));s.mass_g=990;c.tick(s,130);assert(p.enabled);
 s.sampled_at=135;c.tick(s,135);assert(!p.enabled);assert(c.events.back().reason=="dose_time_completed"&&*c.events.back().withdrawn_ml==10);s.mass_g=1000;
 Controller reboot(l,j,p);reboot.connectivity(true);reboot.synchronize();assert(!reboot.submit({"one",150,160,10,5},s,150));
 s.sampled_at=131;assert(!reboot.submit({"new-after-reboot",131,140,10,5},s,131));
 s.sampled_at=160;assert(reboot.submit({"watchdog",160,170,10,5},s,160));reboot.watchdog(161);assert(!p.enabled);
 Controller f(l,j,p);s={true,20,100,0,200,true,1000};f.tick(s,200);assert(p.enabled&&f.events.back().local);f.connectivity(true);assert(!f.synchronize());s.sampled_at=210;f.tick(s,210);assert(!p.enabled&&f.events.back().local);assert(f.synchronize());
 f.connectivity(false);s.sampled_at=240;f.tick(s,240);assert(!p.enabled);s.soil=70;f.tick(s,240);s.soil=20;s.sampled_at=241;f.tick(s,241);assert(p.enabled);s.valid=false;f.tick(s,242);assert(!p.enabled);
 j.fail=true;Controller closed(l,j,p);closed.connectivity(true);closed.synchronize();s={true,40,100,0,300,true,1000};assert(!closed.submit({"journal",300,310,10,5},s,300));
 Buffer<int>b(2);b.push(1);b.push(2);b.push(3);assert(b.dropped==1&&*b.front()==1);b.acknowledged();assert(*b.front()==2);
 RelativeSoilCalibration soil{"test-only",3000,1000};assert(*soil.index(2000)==50);assert(!soil.index(0));assert(*soil.index(900)==100);assert(*soil.index(3150)==0);assert(!soil.index(3300));assert(!soil.index(700));
 assert(*withdrawn_ml(1000,990,1,true)==10);
 // Differential withdrawal works on any tare (negative readings), rejects gains and disturbances.
 assert(*cycle_withdrawal_ml(-600,-650,true,100)==50);assert(!cycle_withdrawal_ml(-600,-590,true,100));assert(!cycle_withdrawal_ml(-600,-750,true,100));assert(!cycle_withdrawal_ml(-600,-650,false,100));
 {MemoryJournal mj;Pump mp;Controller m(l,mj,mp);m.connectivity(true);m.synchronize(5000);Sensors ms{true,40,100,0,5000,true};
  assert(!m.submit({"no-mass",5000,5010,10,5},ms,5000));assert(m.events.back().reason=="mass_unavailable"&&mp.starts==0);
  ms.mass_g=-600;assert(m.submit({"gain",5000,5010,10,5},ms,5000));ms.mass_g=-580;ms.sampled_at=5005;m.tick(ms,5005);
  assert(!mp.enabled&&m.events.back().status=="completed"&&m.events.back().reason=="withdrawal_not_measured"&&!m.events.back().withdrawn_ml);}assert(!withdrawn_ml(1000,990,1,false));assert(!withdrawn_ml(990,1000,1,true));
 PumpEnergy energy(2);assert(energy.add(0,12,1));assert(energy.add(1,12,1));assert(std::abs(*energy.result()-12.0/3600)<1e-10);assert(!energy.add(5,12,1));assert(!energy.result());
 for(int i=0;i<500;++i){closed.submit({"flood",300,310,10,5},s,300);}
 assert(closed.events.size()<=128&&!p.enabled);
 MemoryJournal rj;Pump rp;Controller rc(l,rj,rp);s={true,40,100,0,500,true,1000};rc.connectivity(true);assert(rc.synchronize(500));
 assert(!rc.submit({"before-resync",499,510,10,5},s,500));assert(rc.events.back().reason=="before_resync");
 assert(rc.synchronize(501));assert(rc.submit({"after-resync",500,510,10,5},s,500));rc.watchdog(501);
 JournalState disk;int writes=0;bool storage_ok=true;
 static_assert(sizeof(RetainedJournal)<1024,"Journal snapshots must not live in task stack objects");
 auto persist=[&](const JournalState& next){++writes;if(!storage_ok)return false;disk=next;return true;};
 RetainedJournal retained(disk,persist);assert(retained.observe_time(1000));assert(writes==0);
 for(int i=0;i<128;++i){assert(retained.reserve("id-"+std::to_string(i),1005));}
 assert(!retained.reserve("full",1005));assert(retained.contains("id-0"));
 assert(retained.observe_time(1006));assert(!retained.observe_time(1005));assert(retained.reserve("reclaimed",1010));
 RetainedJournal rebooted(disk,persist);assert(!rebooted.observe_time(1005));assert(rebooted.contains("reclaimed"));
 JournalState legacy;for(auto& slot:legacy.slots){strcpy(slot.id,"legacy-id");}
 RetainedJournal migrated(legacy,persist);assert(migrated.observe_time(2000));assert(!migrated.reserve("new",2010));assert(migrated.observe_time(2299));assert(!migrated.reserve("new",2301));assert(migrated.observe_time(2300));assert(migrated.reserve("new",2301));
 storage_ok=false;assert(!rebooted.reserve("io-failure",1010));assert(rebooted.contains("fail-closed"));
 JournalState corrupt;corrupt.version=99;RetainedJournal invalid(corrupt,persist);assert(!invalid.observe_time(3000));
 std::deque<std::string> event_disk;bool event_ok=true;
 auto checkpoint=[&](const std::deque<std::string>& next){if(!event_ok)return false;event_disk=next;return true;};
 std::string accepted_payload="{\"event_id\":\"12345678-1234-4234-8234-123456789abc\",\"occurred_at\":\"2026-10-12T10:00:00.001Z\"}";
 DurableBuffer persistent(3,event_disk,checkpoint);assert(persistent.push_cycle(accepted_payload,"accepted"));for(int i=0;i<500;++i){assert(!persistent.push_cycle("unrelated","rejected"));}assert(persistent.push_cycle("started","started"));assert(persistent.push_cycle("failed","failed"));
 DurableBuffer restored(3,event_disk,checkpoint);assert(*restored.front()==accepted_payload);assert(restored.acknowledged());assert(*restored.front()=="started");event_ok=false;assert(!restored.acknowledged());assert(event_disk.front()=="started");
 MemoryJournal dj;Pump dp;Controller durable_controller(l,dj,dp,{},[](const Event&){return false;});s={true,40,100,0,4000,true,1000};durable_controller.connectivity(true);durable_controller.synchronize(4000);assert(!durable_controller.submit({"accepted-storage-failed",4000,4010,10,5},s,4000));assert(dp.starts==0);
 // Board clock may lag the server: up to clock_skew_s ahead is accepted, beyond that it is a future command.
 {MemoryJournal j2;Pump p2;Controller c2(l,j2,p2);Sensors s2{true,40,100,0,100,true,1000};c2.connectivity(true);assert(c2.synchronize(0));
  assert(!c2.submit({"far",106,200,10,5},s2,100));assert(c2.events.back().reason=="expired_or_future");
  assert(c2.submit({"skew",105,200,10,5},s2,100));assert(p2.enabled);}
}

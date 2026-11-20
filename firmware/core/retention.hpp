#pragma once
#include "controller.hpp"
#include <array>
#include <cstring>
#include <memory>
#include <new>

namespace irrigation {
struct JournalSlot {char id[96]={};int64_t expires_at=0;};
struct JournalState {
 uint32_t version=1;
 int64_t clock_floor=0,legacy_until=0,cycle_at=0;
 std::array<JournalSlot,128> slots{};
};
// Persist callback must atomically commit the complete candidate before returning true.
class RetainedJournal:public Journal {
 std::unique_ptr<JournalState> state;std::function<bool(const JournalState&)> persist;bool ready=true;
 bool commit(const JournalState& candidate){if(!ready||!persist(candidate)){ready=false;return false;}*state=candidate;return true;}
 std::unique_ptr<JournalState> candidate(){auto next=std::unique_ptr<JournalState>(new(std::nothrow) JournalState(*state));if(!next)ready=false;return next;}
public:
 RetainedJournal(const JournalState& initial,std::function<bool(const JournalState&)> write):state(new(std::nothrow) JournalState(initial)),persist(std::move(write)){ready=state&&state->version==1&&state->clock_floor>=0&&state->legacy_until>=0&&state->cycle_at>=0;if(state)for(auto&s:state->slots)if(!memchr(s.id,0,sizeof(s.id))||s.expires_at<0)ready=false;}
 bool observe_time(int64_t now)override{
  if(!ready||now<=0||now<state->clock_floor)return false;
  state->clock_floor=now;
  if(!state->legacy_until)for(auto&s:state->slots)if(s.id[0]&&!s.expires_at){state->legacy_until=now+300;break;}
  // RAM floor covers observations; durable floor is checkpointed with mutations only.
  // Reboot may restart a legacy quarantine, conservatively delaying reclamation.
  return true;
 }
 bool contains(const std::string&id)override{if(!ready)return true;for(auto&s:state->slots)if(id==s.id)return true;return false;}
 bool reserve(const std::string&id,int64_t expires_at)override{
  if(!ready||id.empty()||id.size()>=96||expires_at<=state->clock_floor||expires_at-state->clock_floor>300||contains(id))return false;
  auto next=candidate();if(!next)return false;
  // Expired entries may all be reclaimed only within the checkpoint that stores this floor.
  for(auto&s:next->slots){bool old=s.id[0]&&!s.expires_at&&state->legacy_until&&state->clock_floor>=state->legacy_until;if((s.expires_at&&s.expires_at<=state->clock_floor)||old)s={};}
  for(auto&s:next->slots){
   bool legacy_expired=s.id[0]&&!s.expires_at&&state->legacy_until&&state->clock_floor>=state->legacy_until;
   if(!s.id[0]||(s.expires_at&&s.expires_at<=state->clock_floor)||legacy_expired){s={};memcpy(s.id,id.c_str(),id.size()+1);s.expires_at=expires_at;return commit(*next);}
  }return false;
 }
 int64_t last_cycle_at()override{return ready?state->cycle_at:INT64_MAX;}
 bool record_cycle_at(int64_t now)override{if(!ready)return false;auto next=candidate();if(!next)return false;next->cycle_at=now;return commit(*next);}
};
}

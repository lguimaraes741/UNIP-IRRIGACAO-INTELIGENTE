#pragma once
#include <deque>
#include <functional>
#include <string>

namespace irrigation {
// Failed checkpoints never remove or replace in-memory pending messages.
class DurableBuffer {
 std::deque<std::string> pending;size_t capacity,reserved=0;
 std::function<bool(const std::deque<std::string>&)> persist;bool ready;
public:
 DurableBuffer(size_t n,std::deque<std::string> initial,std::function<bool(const std::deque<std::string>&)> checkpoint,bool valid=true):pending(std::move(initial)),capacity(n),persist(std::move(checkpoint)),ready(valid&&pending.size()<=n){}
 bool reserve(size_t count){if(!ready||reserved||pending.size()+count>capacity)return false;reserved=count;return true;}
 void release(){reserved=0;}
 bool push(std::string payload,bool use_reserved=false){
  if(!ready||(use_reserved?!reserved:pending.size()+reserved>=capacity))return false;
  auto next=pending;next.push_back(std::move(payload));if(!persist(next)){ready=false;return false;}pending=std::move(next);if(use_reserved)--reserved;return true;
 }
 bool push_cycle(std::string payload,const std::string& status){
  bool accepted=status=="accepted",terminal=status=="completed"||status=="failed";
  if(accepted&&!reserve(3))return false;
  bool saved=push(std::move(payload),accepted||status=="started"||terminal);
  if(terminal||(!saved&&status!="rejected"))release();
  return saved;
 }
 const std::string* front()const{return ready&&!pending.empty()?&pending.front():nullptr;}
 bool acknowledged(){if(!ready||pending.empty())return false;auto next=pending;next.pop_front();if(!persist(next)){ready=false;return false;}pending=std::move(next);return true;}
 size_t size()const{return pending.size();}
 bool available()const{return ready&&pending.size()+reserved<capacity;}
};
}

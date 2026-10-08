# Queue / Playback History Specification

> **สถานะ:** Design Specification / Source of Truth สำหรับการพัฒนาระบบ Queue ต่อไป
>
> เอกสารนี้กำหนดพฤติกรรมของ Queue, Playback History, Previous, Queue UI และ Player UI
> หาก implementation ปัจจุบันขัดกับเอกสารนี้ ให้ยึดเอกสารนี้เป็นเป้าหมาย

## 1. เป้าหมาย

Queue ต้องเป็น rolling playback history:
- เก็บเพลงที่เล่นไปแล้วล่าสุด 10 เพลง
- เก็บเพลงปัจจุบัน
- เก็บเพลงที่ยังไม่ได้เล่นทั้งหมดตามที่เพิ่มเข้ามา
- ห้ามลบ Upcoming เพียงเพราะ Queue มีจำนวนมาก
- เมื่อเพลงที่เล่นไปแล้วเกิน 10 เพลง ให้ลบเฉพาะเพลงเก่าที่เกิน History 10 ออกจาก memory
- Previous ย้อนกลับได้เฉพาะเพลงที่ยังอยู่ใน History
- /queue และ Player ต้องใช้เลขคิวเดียวกัน
- Player แสดง History 10 + Current 1 + Upcoming 5
- ต้องมี marker ระบุเพลงปัจจุบัน
- ทุก state change ต้อง refresh UI ที่เกี่ยวข้อง

## 2. Queue State

ต่อ 1 guild ต้องมี Queue state หลักชุดเดียว แบ่งเป็น:

### History
- เพลงก่อน Current
- เก็บย้อนหลังสูงสุด 10 เพลง
- เพลงที่เก่ากว่านั้นถูก trim ออกจาก memory

### Current
- เพลงที่ now_playing_idx ชี้อยู่
- ห้ามถูก trim
- ต้องมี Current marker
- เป็นจุดอ้างอิงของ Previous / Next / Player / Queue

### Upcoming
- เพลงหลัง Current
- ยังไม่ได้เล่น
- ห้าม trim เพราะ Queue ใหญ่
- เก็บได้ไม่จำกัดตาม logic นี้

## 3. กฎหลัก

### Q1 — History สูงสุด 10
ต้องเป็นจริงเสมอ: 0 <= history_count <= 10

ถ้า Current คือ #12:
  #2-#11 = History 10
  #12      = Current
  #13+     = Upcoming

#1 ต้องถูก trim แล้ว

### Q2 — ห้ามจำกัด Queue รวม
ห้ามใช้ MAX_QUEUE = 20 หรือค่าใด ๆ เป็น hard limit ของ Queue ทั้งหมด
ข้อจำกัดคือ History <= 10 ไม่ใช่ Total Queue <= 20

### Q3 — Upcoming ไม่ถูก trim
ถ้ามี #1-#50 แล้วเพิ่ม #51-#100 ต้องเก็บ #1-#100 ตาม state จริง

### Q4 — Trim เฉพาะ History ที่เกิน 10
เมื่อ Current ขยับจาก #11 เป็น #12:
  #1 -> trim
  #2-#11 -> History
  #12 -> Current
  #13+ -> Upcoming

## 4. ตัวอย่าง

### มี 50 เพลง เล่นถึง #30
  #20-#29 = History 10
  #30      = Current
  #31-#50 = Upcoming 20

ดังนั้น Queue memory มี 31 เพลง: 10 History + 1 Current + 20 Upcoming

หมายเหตุ: หาก requirement อื่นระบุว่าเล่นถึง #30 แล้วต้องแสดง 40 เพลง ตัวเลขนั้นขัดกับสูตรข้างต้น ต้องตรวจความหมายก่อน implementation ห้ามเดา

### มี 100 เพลง เริ่มเล่น
  #1      = Current
  #2-#100 = Upcoming
  History = 0
  Previous = disabled

### มี 100 เพลง เล่น #11
  #1-#10   = History
  #11      = Current
  #12-#100 = Upcoming

### มี 100 เพลง เล่น #30
  #20-#29   = History
  #30       = Current
  #31-#100  = Upcoming
  #1-#19 ถูก trim แล้ว

## 5. Queue Numbering

ต้องแยก list index ออกจาก logical queue number

ตัวอย่างหลัง trim:
  list index 0 -> #20
  list index 1 -> #21
  list index 10 -> #30

get_now_idx() คืน list index
display_no() คืน logical queue number

ห้ามใช้ index + 1 เป็น display number โดยตรงหลังมีการ trim

### Numbering invariant
เลขของเพลงต้องคงที่ตลอดอายุของเพลงใน Queue
ถ้าเพลงคือ #37 ต่อให้ #1-#36 ถูก trim เพลงนั้นยังต้องเป็น #37

## 6. get_full_queue()

หน้าที่: คืน Queue state จริงของ guild

ต้อง:
- คืน state เดียวกับที่ playback ใช้
- ไม่สร้าง Queue copy สำหรับ Player
- ไม่สร้างเลขคิวใหม่
- ไม่แก้ Current

Player และ /queue ต้องอ่าน Queue จาก source เดียวกัน

## 7. get_now_idx()

หน้าที่: คืน list index ของเพลงปัจจุบัน

ตัวอย่าง:
  index 0 = #21
  index 1 = #22
  index 10 = #31

ถ้า #31 เล่นอยู่ get_now_idx() ต้องเป็น 10 ไม่ใช่ 31

## 8. set_now_idx()

หน้าที่: เปลี่ยนตำแหน่ง Current

หลังเปลี่ยนต้องตรวจ:
- index valid หรือไม่
- ต้อง trim History หรือไม่
- Player ต้อง refresh หรือไม่
- Queue views ต้อง refresh หรือไม่

ไม่ควรให้ function นี้สร้างเพลงหรือ append เพลง

## 9. display_no()

หน้าที่: แปลง list index เป็น logical queue number

แนวคิด:
  display_number = sequence_offset + index + 1

เมื่อ trim เพลงด้านหน้า:
  sequence_offset += จำนวนที่ trim

queue_seq_offset ที่มีอยู่ใน code ปัจจุบันเป็นแนวคิดที่เหมาะสมและควรรักษาไว้

## 10. add_to_queue()

หน้าที่: เพิ่มเพลงท้าย Queue

ต้อง:
1. append ท้าย Queue
2. ไม่เปลี่ยน Current
3. ไม่เปลี่ยน History
4. ไม่ลบ Upcoming
5. กำหนด logical number ใหม่
6. refresh Queue UI
7. refresh Player ถ้า Next list เปลี่ยน

ตัวอย่าง Current #30 เพิ่มเพลง 51-53 ต้องได้ #20-#53 โดยไม่เปลี่ยน Current

## 11. _trim_queue()

หน้าที่: ลบเฉพาะเพลงที่เกิน History 10 เพลง

ห้ามทำ:
  if len(queue) > 20: delete_from_front()

เพราะจะลบเพลง Upcoming

หลัก:
  history_count = current_index
  ถ้า history_count > 10:
      trim_count = history_count - 10
      ลบจากด้านหน้า trim_count

หลัง trim ต้อง:
1. ลด now_playing_idx ตามจำนวนที่ลบ
2. เพิ่ม sequence offset
3. ปรับ mapping ของ message ที่ผูกกับ mutable index ถ้ายังมี
4. ห้ามเปลี่ยน logical number ของเพลงที่เหลือ
5. refresh UI

หลัง function จบ history_count ต้อง <= 10

## 12. Previous

Previous ทำได้เมื่อ current_index > 0

ถ้า current_index == 0:
- Previous ต้อง disabled หรือ reject
- ห้ามเปลี่ยน Queue
- ห้าม append เพลง
- ห้ามสร้าง duplicate

เมื่อ Previous สำเร็จ Current ต้องขยับไป index ก่อนหน้า และเพลงเดิมที่เคยเป็น Current ต้องยังอยู่ใน Queue

## 13. Previous แล้ว Next

ตัวอย่าง:
  #20
  #21
  #22 CURRENT
  #23
  #24

Previous -> #21
Next -> #22

ต้องใช้ #22 ที่มีอยู่เดิม ห้าม append #22 กลับท้าย Queue

## 14. Skip / Next

เมื่อ Skip หรือเพลงจบ:
1. เปลี่ยน Current ไปเพลงถัดไป
2. ตรวจ/trim History
3. เริ่ม playback
4. refresh Player
5. refresh Queue views
6. update Previous availability

ลำดับแนะนำ:
  choose next -> set current -> trim history -> start playback -> refresh Player -> refresh Queue

## 15. Natural Song End / play_next()

callback ของเพลงที่จบต้องทำ state transition แบบเดียวกับ Next

ต้องรองรับ:
- natural end
- playback error
- loop track
- loop queue
- next track
- queue end
- playback generation

ต้องรักษา playback generation / lock ที่มีอยู่ เพื่อป้องกัน callback เก่าเขียน state ทับเพลงใหม่

กรณี A ถูก Skip ไป B แล้ว callback ของ A มาถึงช้า callback ของ A ต้องถูก ignore

## 16. make_queue_embed()

หน้าที่: render Queue สำหรับ /queue

ต้องแสดง:
- logical queue number
- title
- Current marker
- pagination 20 รายการต่อหน้า

ตัวอย่าง:
  #21 🎵 Song 21
  #22 🎵 Song 22
  #23 🎵 Song 23
  #24 ▶️ Song 24
  #25 🎵 Song 25

ห้ามเริ่มเลขใหม่ตามหน้า เช่น 1 Song 21, 2 Song 22

## 17. Current Marker

ต้องมี marker ชัดเจนว่าเพลงไหนกำลังเล่น

ใช้ pattern เดียวกัน เช่น ▶️

ต้องเป็นจริง:
- มี Current marker เพียงเพลงเดียว
- History ไม่มี marker
- Upcoming ไม่มี marker
- หลัง Skip marker ย้ายทันที
- หลัง Previous marker ย้ายทันที
- หลังเพลงจบ marker ย้ายทันที

## 18. Queue Pagination

QUEUE_PAGE_SIZE ยังคงเป็น 20

ตัวอย่าง Queue #21-#100:
  Page 1 = #21-#40
  Page 2 = #41-#60
  Page 3 = #61-#80
  Page 4 = #81-#100

หลัง trim ต้อง refresh pagination และถ้า page เดิมไม่ valid ให้ย้ายไป page ที่ valid

## 19. _refresh_queue_msg()

หน้าที่: refresh Queue views ที่เปิดอยู่ทั้งหมดของ guild

ต้อง:
1. อ่าน Queue ล่าสุด
2. อ่าน Current ล่าสุด
3. คำนวณ logical number
4. คำนวณ Current marker
5. คง page เดิมถ้ายัง valid
6. ปรับ page ถ้าเกิน
7. sync buttons
8. edit message

ห้ามใช้ Queue snapshot เก่า

## 20. make_now_playing_embed()

Player ต้องอ่าน Queue state เดียวกับ /queue

ถ้า Current = #30:
  History: #20-#29
  Current: #30
  Upcoming: #31-#35

สูงสุด 16 เพลง:
  10 History + 1 Current + 5 Upcoming

ถ้ามีไม่ถึงจำนวนดังกล่าวให้แสดงเท่าที่มี

## 21. Player Numbering

ถ้า /queue แสดง:
  #27 Song A
  #28 Song B
  #29 ▶️ Song C
  #30 Song D

Player ต้องใช้เลขเดียวกัน

ห้าม Player สร้าง counter ใหม่ เช่น 01, 02, 03

## 22. _refresh_player()

ต้อง refresh เมื่อ state ที่ผู้ใช้มองเห็นเปลี่ยน:
- เพลงใหม่เริ่มเล่น
- เพลงจบ
- Skip
- Previous
- Volume
- Pause
- Resume
- Loop mode
- Shuffle
- เพิ่มเพลงใน Queue
- Queue trim
- Current index เปลี่ยน

ถ้า Player message เดิมอยู่ ให้ edit message เดิม ไม่สร้างใหม่ทุกครั้ง

## 23. Volume

เมื่อเปลี่ยน Volume:
  เปลี่ยน source volume
  -> update guild volume state
  -> refresh Player
  -> respond interaction

ต้องไม่สร้าง Player message ใหม่เพราะ Volume

ผู้ใช้ยังต้องได้รับ confirmation เช่น: ระดับเสียง 50%

## 24. clear_guild()

ใช้เมื่อ session จบ เช่น Stop หรือ disconnect จริง

ต้องล้าง:
- Queue
- Current index
- sequence offset
- total-added state ถ้ามี
- volume
- loop
- shuffle
- active Player
- Queue view references
- message references ที่เกี่ยวข้อง

หลัง clear:
  Queue = empty
  History = empty
  Current = none
  Upcoming = empty

## 25. Stop vs Natural End

### Stop
ต้อง clear session ทั้งหมด และ Previous ใช้งานไม่ได้

### Natural End แต่ยังมีเพลง
ห้าม clear Queue
Current -> next
History -> เก็บสูงสุด 10
Upcoming -> เก็บทั้งหมด

## 26. Loop Track

Loop Track ไม่ควรสร้าง duplicate queue entry

ถ้า #30 เล่นซ้ำ:
  #30 ยังเป็น Current

ไม่ควรสร้าง #31 copy ของ #30

## 27. Loop Queue

ถ้า Queue หมดและ Loop Queue เปิด ให้กำหนด behavior กับ Queue ที่ยังอยู่ใน memory เท่านั้น

เพลงที่ถูก trim ไปแล้วไม่มีสิทธิ์กลับมาโดยอัตโนมัติ

ถ้าต้องการ archive สำหรับ Loop ต้องออกแบบเป็น feature แยก

## 28. Shuffle

Shuffle ต้องไม่ทำลาย logical queue number

ก่อน:
  #20 A
  #21 B
  #22 C
  #23 D

หลัง shuffle playback order อาจเป็น #23, #21, #20, #22 แต่เลขยังเป็นเลขเดิม

ห้าม renumber เป็น #1-#4

## 29. Queue Add Message Mapping

ถ้า message ผูกกับ mutable list index ต้อง shift mapping เมื่อ trim

ระยะยาวควรผูก message กับ stable queue ID หรือ logical number แทน list index

## 30. Concurrency

Queue state อาจถูกแก้พร้อมกันจาก:
- Voice callback
- Skip
- Previous
- /play
- Playlist add
- Queue button
- Stop
- External disconnect

การเปลี่ยน Queue/Current/playback ต้องใช้ lock หรือ generation guard ที่มีอยู่

หลักสำคัญ:
> callback เก่าห้ามเขียน state ทับ callback ใหม่

## 31. UI Consistency Invariant

ต้องเป็นจริงเสมอ:
  Queue current = Player current = now_playing_idx = VoiceClient current playback

ถ้าไม่ตรง ถือเป็น bug

## 32. Numbering Consistency Invariant

เพลงเดียวกันต้องมีเลขเดียวกันทุก UI:
  /queue: #73
  Player: #73

ห้าม /queue #73 แต่ Player #03

## 33. History Invariant

หลัง state transition ทุกครั้ง:
  0 <= history_count <= 10

และ:
  history_count = number of tracks before current

## 34. Upcoming Invariant

Upcoming คือเพลงหลัง current:
  queue[current_index + 1:]

Player ใช้เพียง 5 รายการแรก แต่ Queue state ต้องเก็บ Upcoming ทั้งหมด

## 35. สิ่งที่ห้ามทำ

1. ใช้ MAX_QUEUE=20 เป็น hard limit ของ Queue รวม
2. ใช้ list index + 1 เป็นเลข Queue หลัง trim
3. สร้าง counter แยกสำหรับ Player
4. สร้าง Queue copy ที่มีเลขของตัวเองสำหรับ Player
5. ลบ Upcoming เพราะ Queue ใหญ่
6. Previous แล้ว append เพลงเดิมท้าย Queue
7. ให้ callback เก่าแก้ Current หลัง Skip/Previous
8. แก้ UI โดยไม่แก้ source state
9. ให้ /queue และ Player คำนวณ Current แยกกัน
10. เปลี่ยน logical number ของเพลงที่ยังอยู่ใน Queue

## 36. Function Responsibility

| Function | หน้าที่ | ห้ามรับผิดชอบ |
|---|---|---|
| get_full_queue() | คืน Queue state | สร้างเลข UI |
| get_now_idx() | คืน current list index | คืน display number |
| set_now_idx() | เปลี่ยน current index | สร้างเพลง |
| display_no() | index -> logical number | แก้ Queue |
| add_to_queue() | เพิ่มเพลงท้าย | ลบ Upcoming |
| _trim_queue() | จำกัด History <= 10 | จำกัด Queue รวม |
| clear_guild() | reset session | เก็บ History หลัง Stop |
| make_queue_embed() | render Queue | แก้ state |
| _refresh_queue_msg() | update Queue UI | สร้าง Queue ใหม่ |
| make_now_playing_embed() | render Player | แก้ state |
| _refresh_player() | update Player | สร้าง Queue แยก |
| play_next() | transition ไปเพลงถัดไป | ใช้ callback เก่า |
| Previous handler | transition ไปเพลงก่อนหน้า | append duplicate |
| Skip handler | transition ไปเพลงถัดไป | bypass generation guard |
| Queue pagination | เปลี่ยนหน้า UI | เปลี่ยน Current |

## 37. Acceptance Tests

### Test 1 — Initial queue
เพิ่ม 50 เพลง:
  History = 0
  Current = #1
  Upcoming = #2-#50
  Previous = disabled

### Test 2 — Current #11
  History = #1-#10
  Current = #11
  Upcoming = #12-#50
  Previous = available

### Test 3 — Current #12
  #1 = trimmed
  History = #2-#11
  Current = #12

### Test 4 — Add 50 while current #30
ถ้าเดิม #1-#50 แล้วเพิ่ม #51-#100:
  History = 10
  Current = 1
  Upcoming = 70
ไม่มี Upcoming ถูก trim

### Test 5 — Previous
กด Previous 10 ครั้ง:
- ย้อนกลับได้ 10 เพลง
- ครั้งที่ 11 ใช้ไม่ได้
- ไม่มี duplicate
- Queue number ไม่เปลี่ยน

### Test 6 — Previous then Next
Previous จาก #22 -> #21
Next ต้องกลับ #22 โดยไม่สร้าง duplicate

### Test 7 — Skip
Expected: Current เปลี่ยน, History update, old history trim, Player update, Queue update

### Test 8 — Natural end
Expected: behavior ด้าน Queue เหมือน Next และ old callback ไม่ชน Current ใหม่

### Test 9 — Player
ถ้า Current = #30:
  History = #20-#29
  Current = #30
  Next = #31-#35

### Test 10 — Pagination
Queue #21-#100:
  Page 1 = #21-#40
  Page 2 = #41-#60
  Page 3 = #61-#80
  Page 4 = #81-#100

### Test 11 — Trim while Queue view open
เปิด /queue ค้างไว้ แล้ว Current เดินจน trim:
- Queue message refresh
- number ถูกต้อง
- marker ถูกต้อง
- page ยัง valid

### Test 12 — Volume
เปลี่ยน 10% -> 50%:
- source volume = 50%
- Player แสดง 50%
- ไม่สร้าง Player message ใหม่
- interaction ได้รับ confirmation

## 38. Implementation Order

1. Queue data/state
2. _trim_queue()
3. now_playing_idx
4. logical numbering
5. Previous
6. Skip
7. natural playback callback
8. make_queue_embed()
9. Queue pagination
10. make_now_playing_embed()
11. _refresh_player()
12. _refresh_queue_msg()
13. Loop
14. Shuffle
15. queue-add message references
16. race-condition checks
17. acceptance tests

ห้ามเริ่มจาก UI ก่อน Queue state ถูกต้อง

## 39. Current Code Compatibility Note

Implementation ปัจจุบันมี MAX_QUEUE = 20 และ queue_seq_offset

queue_seq_offset เป็นแนวคิดที่เหมาะกับการรักษาเลขคิวหลัง trim และควรรักษาไว้

แต่ MAX_QUEUE = 20 ไม่ตรงกับ specification นี้ เพราะผูกการ trim กับจำนวน Queue รวม

เป้าหมายใหม่:
  History limit = 10
  Upcoming limit = ไม่มี
  Queue total limit = ไม่มีใน logic นี้

## 40. Final Architecture

                Queue State
                    |
        +-----------+-----------+
        |           |           |
      /queue      Player     Previous
        |           |           |
        +-----------+-----------+
                    |
              Playback state
                    |
             +------+------+
             |             |
           Skip         Song End
             |             |
             +------+------+

                    |
               Next Current
                    |
               Trim History
                    |
              Refresh all UI

Source of truth ต้องเป็น Queue state เดียว

Player และ /queue เป็นคนละ view ของ state เดียวกัน

## Final Rule

> Queue คือ state เดียว
>
> Index คือที่อยู่ใน memory
>
> Logical number คือหมายเลขที่ผู้ใช้เห็น
>
> History เก็บย้อนหลังสูงสุด 10 เพลง
>
> Upcoming เก็บทั้งหมด
>
> Player และ /queue อ่าน state เดียวกัน
>
> ไม่มี UI ไหนสร้างหมายเลขคิวของตัวเอง

## 41. Discord UI Contract

เอกสารนี้เป็นข้อกำหนดของ UI ที่แสดงจริงใน Discord ไม่ใช่ Web UI

### 41.1 Main Player

Main Player ใช้ Discord Embed + Discord UI View/Buttons โดยโครงสร้างเป้าหมายคือ:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎵  NOW PLAYING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
เพลงที่กำลังเล่น
_Artist_ • YouTube
👤 SEA_Beach  •  ⏱ 3:42
🔊 10%  •  🔀  •  🔂
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📚 HISTORY • LAST 10
01 🎧 เพลงก่อนหน้า 1                  `3:21`
02 🎵 เพลงก่อนหน้า 2                  `4:05`
03 🎶 เพลงก่อนหน้า 3                  `2:58`
04 🎼 เพลงก่อนหน้า 4                  `3:44`
...

▶️ CURRENT
05 ▶️ เพลงที่กำลังเล่น                 `3:42`

⏭️ NEXT • 5
06 🎧 เพลงถัดไป 1                     `4:12`
07 🎵 เพลงถัดไป 2                     `3:36`
08 🎶 เพลงถัดไป 3                     `5:01`
09 🎼 เพลงถัดไป 4                     `2:49`
10 🎧 เพลงถัดไป 5                     `3:55`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
กำลังเล่น #5 จาก 20 เพลง

ปุ่มหลัก (Discord UI จริง):
[ 🔀 ] [ ⏮ ] [ ⏸ ] [ ⏭ ] [ ⏹ ]
[ 🔁 ] [ 🔍 ] [ 📋 ] [ 🔊 ]

Discord จำกัด Button ได้สูงสุด 5 ปุ่มต่อ row ดังนั้นต้องแบ่งปุ่มเป็น 2 rows ตามด้านบน

ความหมาย:
- 🔀 = Shuffle เฉพาะ Upcoming
- ⏮ = Previous
- ⏸ / ▶️ = Pause / Resume
- ⏭ = Next / Skip
- ⏹ = Stop
- 🔁 / 🔂 = Repeat mode: Queue / Track ตาม state
- 🔍 = Search
- 📋 = เปิด Queue Page แยก
- 🔊 = Volume

สถานะปุ่ม:
- Shuffle ปิด = secondary, เปิด = success
- Repeat ปิด = 🔁 / secondary
- Repeat Track = 🔂 / success
- Repeat Queue = 🔁 / success

หมายเหตุสำคัญ:
- เลข 01, 02, ... ในตัวอย่างเป็นลำดับที่แสดงใน Player ไม่ใช่ logical queue number
- Logical queue number ที่ใช้ระบุเพลงจริงต้องยังคงตรงกับ /queue
- Main Player ต้องแสดง History สูงสุด 10 + Current 1 + Next สูงสุด 5
- History/Current/Next ต้องมาจาก Queue state เดียวกัน

### 41.2 Queue Page

Queue Page คือหน้าที่เปิดจากปุ่ม 📋 โดยเป็น UI แยกจาก Main Player และมีหน้าที่แสดง Queue ทั้งหมดตาม pagination

ตัวอย่างเป้าหมาย:

[📋] QUEUE
01 ▶️ PREP - Who's Got You Singing A… `4:50`
02 🎵 PREP - "As It Was" (Harry Styl… `3:46`
03 🎶 pami - pity dirty (Official Vi… `3:22`
04 🎼 PREP - Cheapest Flight `4:29`
05 🎧 PREP - Line By Line feat. Cory… `3:58`
...
20 🎼 Rex Orange County - AMAZING (O… `4:20`
Page 1 / 3  •  50 songs  •  กำลังเล่น #1

ข้อกำหนด:
- QUEUE แสดง 20 รายการต่อหน้า
- ต้องแสดงชื่อเพลง + duration
- ต้องแสดงสถานะเพลงปัจจุบัน
- ต้องแสดงจำนวนเพลงทั้งหมด
- ต้องแสดง page ปัจจุบัน/จำนวนหน้าทั้งหมด
- ต้องแสดง logical queue number ตาม state จริงเมื่อระบบใช้ logical numbering
- ปุ่ม pagination ต้องเป็น Discord buttons และเปลี่ยนเฉพาะหน้าที่แสดง ไม่เปลี่ยน Current

### 41.3 Current Marker

`▶️` เป็น dynamic Current marker

ห้าม hard-code ให้ #1 เป็น ▶️ ตลอดเวลา

ตัวอย่างเมื่อ #1 เล่น:
01 ▶️ Song A
02 🎵 Song B
03 🎶 Song C

เมื่อ Current เปลี่ยนเป็น #2:
01 🎧 Song A
02 ▶️ Song B
03 🎶 Song C

เมื่อ Current เปลี่ยนเป็น #7:
- marker ต้องย้ายไป #7
- #1 ต้องไม่แสดง ▶️
- Queue Page footer ต้องแสดง `กำลังเล่น #7`
- Player ต้องเปลี่ยน Current/History/Next ให้ตรงกับ #7

ต้องมี Current marker เพียงหนึ่งรายการต่อ Queue state

### 41.4 Discord Component Rules

UI ทุกส่วนต้องอิง Discord API ที่รองรับจริง:
- `discord.Embed`
- `discord.ui.View`
- `discord.ui.Button`
- `discord.ui.Modal`
- `Interaction`
- `interaction.response`
- `interaction.followup`
- `interaction.edit_original_response()`
- `message.edit()`

ห้ามออกแบบปุ่มหรือ interaction ที่ Discord รองรับไม่ได้

การเปลี่ยน UI ต้องคำนึงถึง:
- button row/component limits ของ Discord
- interaction acknowledgement
- ephemeral response
- message edit vs followup message
- View timeout/persistence ตามการใช้งานจริง

## 42. UI Event Synchronization Contract

Main Player และ Queue Page ต้องเป็นคนละ View ของ Queue/Playback state เดียวกัน

ทุก event ที่เปลี่ยน state ที่ผู้ใช้มองเห็นต้องเรียก refresh mechanism กลาง ไม่ควรแก้ Embed หรือ Button เฉพาะจุดแล้วปล่อยอีก UI เป็นข้อมูลเก่า

### Events ที่ต้อง refresh

- เริ่มเพลงใหม่
- เพลงจบตามธรรมชาติ
- Skip / Next
- Previous
- Pause
- Resume
- Stop
- Volume เปลี่ยน
- Shuffle
- Loop mode เปลี่ยน
- เพิ่มเพลงเข้า Queue
- เพลงถูกนำออก/Queue เปลี่ยน
- Queue ถูก consume
- History ถูก trim
- Queue ว่าง
- Playback error แล้วข้ามเพลง
- Voice connection/disconnect ที่ทำให้ playback state เปลี่ยน

### Refresh rule

State change
→ update source state
→ validate invariants
→ render Main Player
→ render Queue Page ที่เปิดอยู่
→ sync button state
→ edit Discord messages

ห้าม:
- update UI ก่อน source state เสร็จ
- ใช้ snapshot เก่า
- ให้ Player กับ Queue Page คำนวณ Current แยกกัน
- ให้ UI หนึ่งอัปเดต แต่อีก UI ค้าง state เดิม

## 43. Player Button Logic Contract

### 🔀 Shuffle
- Shuffle เฉพาะเพลงที่อยู่ใน Upcoming (`queue[current_index + 1:]`)
- ห้ามเปลี่ยน Current
- ห้าม shuffle History
- ห้ามสร้างหรือ duplicate Queue entry
- ห้าม renumber logical queue number
- หาก Upcoming มีน้อยกว่า 2 เพลง ให้ reject/แจ้งผู้ใช้
- หลัง Shuffle ต้อง refresh Main Player + Queue Page

### 🔁 Repeat
Repeat มี 3 states และวนตามลำดับ:
  off → track → queue → off

- **off:** เล่นตาม Queue ปกติ
- **track:** เล่นเพลง Current ซ้ำ โดยใช้ Queue entry เดิม ห้าม append duplicate
- **queue:** เมื่อถึงท้าย Queue ให้กลับไปเล่นเพลงแรกที่ยังอยู่ใน memory
- เพลงที่ถูก trim ออกจาก History แล้วจะไม่ถูกนำกลับมาโดยอัตโนมัติ
- หลังเปลี่ยน Repeat mode ต้อง refresh Main Player + Queue Page
- ปุ่มแสดง state ด้วย emoji/style ที่ตรงกับ mode: off = 🔁, track = 🔂, queue = 🔁

### ⏮ Previous
### ⏮ Previous
- ใช้ Current index ปัจจุบัน
- ถ้าไม่มี History/ไม่มีเพลงก่อนหน้า ให้ disabled หรือ reject
- เมื่อสำเร็จ Current ต้องย้ายไปเพลงก่อนหน้า
- เพลงเดิมที่เป็น Current ต้องยังอยู่ใน Queue
- ห้าม append เพลงเดิมท้าย Queue
- ต้อง refresh Player + Queue Page

### ⏸ / ▶️ Pause / Resume
- Pause เมื่อกำลังเล่น
- Resume เมื่อ paused
- เปลี่ยนสถานะปุ่มตาม playback state จริง
- ต้อง refresh Embed ด้วย เพราะสถานะ Player เปลี่ยน
- ห้ามแก้เฉพาะปุ่มแล้วปล่อย Embed เก่า

### ⏭ Next / Skip
- เปลี่ยน Current ไปเพลงถัดไป
- ต้องใช้ transition/generation guard
- ต้องหยุด source เดิมโดยไม่ให้ callback เก่าเปลี่ยน Current ซ้ำ
- ต้อง refresh Player + Queue Page

### ⏹ Stop
- หยุด playback
- clear guild session ตาม clear_guild()
- Queue/History/Current ต้องถูกล้างตาม specification
- Player controls ต้องถูก disable หรือ message ถูกจัดการตาม implementation
- Queue Page ที่เปิดอยู่ต้องไม่แสดง Current เก่าหลัง state ถูก clear

### 🔍 Search
- เปิด Discord Modal
- รับชื่อเพลงหรือ YouTube URL
- ผลค้นหาต้องผ่าน Discord UI ที่รองรับจริง
- การเพิ่มเพลงต้องแก้ source Queue ก่อน แล้ว refresh UI

### 📋 Queue
- เปิด Queue Page แยก
- ไม่ควรใช้ Queue snapshot แบบถาวร
- หน้า Queue ต้องอ่าน state ล่าสุดทุกครั้งที่ render/refresh

### 🔊 Volume
- รับค่าระหว่าง 0-100
- เปลี่ยน volume ของ audio source
- บันทึก guild volume state
- refresh Player เพื่อแสดงค่าปัจจุบัน
- ส่ง confirmation ให้ผู้ใช้
- ไม่สร้าง Player message ใหม่เพียงเพราะ Volume เปลี่ยน

## 44. Playback Transition Contract

การเปลี่ยนเพลงต้องมี transition เดียวสำหรับทุกเส้นทางหลัก:
- Natural End
- Next/Skip
- Previous
- Playback error

ห้ามมี logic คนละชุดที่แก้ Queue/Current คนละแบบ

### Transition sequence

1. ตรวจว่า transition request ยัง valid
2. invalidate/ignore callback ของ playback generation เดิมถ้าจำเป็น
3. เลือก target track
4. update Current
5. trim History
6. start target playback
7. update playback state
8. refresh Main Player
9. refresh Queue Page
10. update button availability

### Race condition

กรณี:
A กำลังเล่น → ผู้ใช้กด Skip → เริ่ม B → callback ของ A มาถึงภายหลัง

ผลที่ถูกต้อง:
- callback ของ A ต้องถูก ignore
- Current ต้องยังเป็น B
- Queue number ของ B ต้องไม่เปลี่ยน
- Player ต้องแสดง B
- Queue Page ต้องแสดง ▶️ ที่ B

กรณี:
A กำลังเล่น → Previous ไป P → callback ของ A มาถึงภายหลัง

ผลที่ถูกต้อง:
- callback ของ A ต้องไม่เรียก Next ซ้ำ
- Current ต้องยังเป็น P

## 45. UI Numbering Clarification

มี numbering สองชนิดและห้ามสับสน:

1. **Logical Queue Number**
   - หมายเลขถาวรของเพลงใน Queue
   - ใช้ร่วมกันระหว่าง Queue state และ /queue
   - ไม่เปลี่ยนเมื่อ History ถูก trim

2. **Player Display Position**
   - ตำแหน่งภายในส่วน History/Current/Next ของ Player
   - เช่น 01-16 ตามรายการที่กำลังแสดง
   - เปลี่ยนได้ตาม Current
   - ห้ามนำไปใช้แทน logical queue number

ดังนั้น:
- /queue อาจแสดง #73
- Player อาจแสดงเพลงเดียวกันเป็นรายการที่ 11 ภายใน Player
- แต่ข้อมูลอ้างอิงเพลงต้องยังเป็น logical #73

## 46. UI State Examples

### Current #1

Queue Page:
01 ▶️ Song A
02 🎵 Song B
03 🎶 Song C

Footer:
Page 1 / 3 • 50 songs • กำลังเล่น #1

Player:
History = none
Current = #1
Next = #2-#6

### Current #2

Queue Page:
01 🎧 Song A
02 ▶️ Song B
03 🎶 Song C

Footer:
Page 1 / 3 • 50 songs • กำลังเล่น #2

Player:
History = #1
Current = #2
Next = #3-#7

### Current #21

Queue Page:
- Page 2 ต้องเป็นหน้าที่แสดง Current #21 ตาม pagination ของ Queue state
- marker ▶️ ต้องอยู่ที่ #21
- footer ต้องแสดง กำลังเล่น #21

Player:
History = #11-#20
Current = #21
Next = #22-#26

### Current #30

Queue Page:
- marker ▶️ อยู่ที่ #30
- footer แสดง กำลังเล่น #30

Player:
History = #20-#29
Current = #30
Next = #31-#35

## 47. UI Acceptance Tests

### UI Test 1 — Current marker moves
เริ่ม #1 → marker อยู่ #1
เปลี่ยนเป็น #2 → marker ย้าย #2
เปลี่ยนเป็น #7 → marker ย้าย #7
ต้องไม่มี ▶️ ซ้ำ

### UI Test 2 — Previous/Next sync
Current #22
→ Previous
→ Player = #21
→ Queue Page marker = #21
→ Next
→ Player = #22
→ Queue Page marker = #22
→ ไม่มี duplicate queue entry

### UI Test 3 — Natural end sync
#30 จบ → #31 เริ่ม
ต้องพร้อมกัน:
- Current = #31
- Player Current = #31
- Queue marker = #31
- footer = กำลังเล่น #31
- History/Upcoming ถูกคำนวณใหม่

### UI Test 4 — Volume sync
10% → 50%
- source = 50%
- Player = 50%
- Queue state ไม่เปลี่ยน
- Player message เดิมถูก edit

### UI Test 5 — Queue add sync
Current #30
เพิ่ม #51
- Current ยัง #30
- Queue มี #51
- Player Next list ถ้าจำเป็นต้องแสดงการเปลี่ยน
- Queue Page แสดง #51 ตามหน้าที่ถูกต้อง

### UI Test 6 — Pagination + Current
Current อยู่ #21
Queue Page ต้องเปิด/ย้ายไปหน้าที่มี #21 เมื่อ requirement ของ view ระบุให้ตาม Current
marker ต้องอยู่ #21

### UI Test 7 — Trim + open Queue
Queue Page เปิดค้าง
Current เดินจาก #11 → #12
#1 ถูก trim
Queue Page refresh
logical numbers #2-#... ยังถูกต้อง
Current marker ไม่ผิดตำแหน่ง

## 48. Documentation Rule for Future Development

เมื่อมีการเพิ่มหรือแก้ UI/Logic:
1. ตรวจเอกสารนี้ก่อน
2. ตรวจ source state/implementation ปัจจุบัน
3. ถ้า behavior ใหม่ขัดกับเอกสาร ให้ระบุความขัดแย้งก่อนแก้
4. แก้ source state ก่อน UI เมื่อเป็น state/logic issue
5. ให้ UI render จาก source state
6. เพิ่ม/ปรับ acceptance test
7. อัปเดตเอกสารนี้เมื่อ contract เปลี่ยน

เมื่อมีคำถามว่า "UI ทำงานอย่างไร" หรือ "logic ของปุ่ม/Queue เป็นอย่างไร" ให้ตอบจาก specification นี้และ implementation ปัจจุบันร่วมกัน โดยแยกให้ชัดว่า:
- **Specified:** สิ่งที่เอกสารกำหนด
- **Implemented:** สิ่งที่ code ทำอยู่จริง
- **Bug/Gap:** จุดที่ code ยังไม่ตรง specification

ห้ามเดา behavior ที่ไม่มีใน specification หรือ source code

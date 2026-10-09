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
- Player แสดง History 3 + Current 1 + Upcoming 3; ประวัติจริงสำหรับ Previous ยังคงเก็บสูงสุด 10 เพลง
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

## 20. Player Rendering

Player ต้องอ่าน Queue state เดียวกับ /queue

ถ้า Current = #30:
  History ที่แสดง: #27-#29
  Current: #30
  Upcoming ที่แสดง: #31-#33

Player แสดงสูงสุด 7 เพลง:
  3 History + 1 Current + 3 Upcoming
  แต่ Previous ยังใช้ History จริงย้อนหลังได้สูงสุด 10 เพลง

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

การอัปเดตทั่วไป เช่น Volume, Pause/Resume, Loop, Shuffle และสถานะการเล่น ให้ edit Player message เดิม\n\nเมื่อมีเพลงใหม่ถูกเพิ่มเข้า Queue ขณะ Player ทำงานอยู่ ให้ลบ Player message เดิมแล้วส่ง Player message ใหม่ เพื่อให้ไปอยู่ล่างสุดของช่องข้อความ Discord\n\nการเพิ่มหลายเพลงติด ๆ กันต้อง debounce/coalesce ให้เกิดการ repost เพียงครั้งเดียวหลังการเพิ่มหยุดลงช่วงสั้น ๆ ห้ามสร้าง Player message ใหม่ทุกครั้งที่เพลงแต่ละเพลงถูกเพิ่ม

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
👤 SEA_Beach  •  ⏱ 3:42\n━━━━━━━━●━━━━━━━━
🔊 10%  ▰▱▱▱▱▱▱▱▱▱  •  🔀  •  🔂
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📚 HISTORY • LAST 10
01 ♫ เพลงก่อนหน้า 1                  `3:21`
02 ♫ เพลงก่อนหน้า 2                  `4:05`
03 ♫ เพลงก่อนหน้า 3                  `2:58`
04 ♫ เพลงก่อนหน้า 4                  `3:44`
...

▶️ CURRENT
05 ▶️ เพลงที่กำลังเล่น                 `3:42`

⏭️ NEXT • 5
06 ♫ เพลงถัดไป 1                     `4:12`
07 ♫ เพลงถัดไป 2                     `3:36`
08 ♫ เพลงถัดไป 3                     `5:01`
09 ♫ เพลงถัดไป 4                     `2:49`
10 ♫ เพลงถัดไป 5                     `3:55`
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
→ edit existing messages for ordinary state changes
→ for Queue additions, debounce rapid updates then delete and repost the Main Player once

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

### Test 8 — External Voice Disconnect
A กำลังเล่นอยู่ และอาจมี Playlist background fetch ทำงาน
→ ผู้ใช้ตัด Bot ออกจาก Voice Channel
→ ระบบตรวจพบ External Disconnect
→ Player session เดิมถูก invalidate
→ playback generation เดิมถูก invalidate
→ background Playlist fetch เดิมถูก invalidate
→ Queue / History / Current ถูก clear
→ Queue/Search UI references ของ session เดิมถูกล้าง
→ /play B
→ สร้าง session ใหม่สำหรับ B
→ callback ของ A มาถึงภายหลัง
→ callback A ต้องถูก ignore
→ Playlist task ของ A ที่ยังจบภายหลังต้องไม่เติมเพลง
→ Current ต้องยังเป็น B
→ Queue ต้องมีเฉพาะ state ของ session ใหม่

### Test 9 — External Disconnect ระหว่างกำลังเพิ่ม Playlist
เริ่มเพิ่ม Playlist
→ เพลงแรกเริ่มเล่น
→ เพลงที่เหลือกำลัง fetch ใน background
→ Bot ถูกตัดออกจาก Voice
→ งาน fetch ที่กำลังรออยู่ต้องไม่ commit ผลกลับเข้า Queue หลัง session ถูก invalidate
→ /play ใหม่ต้องสร้าง state ใหม่โดยไม่รับผลจาก Playlist เดิม

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

## 49. Complete Component ID & Scenario Contract

### 49.1 Main Player IDs

ลำดับปุ่ม Main Player เป็น contract ของ UI และต้องคงลำดับนี้:

**Row 0**
`[ ⏮ ] [ ⏸ ] [ ⏭ ] [ ⏹ ] [ 🔁 ]`

**Row 1**
`[ 🔍 ] [ 📋 ] [ 🔊 ] [ 🔀 ]`

| ลำดับ | UI | custom_id | หน้าที่ |
|---|---|---|---|
| 1 | ⏮ Previous | player_previous | เล่นเพลงก่อนหน้าใน History |
| 2 | ⏸ / ▶️ Pause/Resume | player_pause_resume | pause/resume Current |
| 3 | ⏭ Next | player_skip | ไปเพลงถัดไป |
| 4 | ⏹ Stop | player_stop | หยุดและ clear session |
| 5 | 🔁 / 🔂 Repeat | player_loop | off → track → queue → off |
| 6 | 🔍 Search | player_search | เปิด Search Modal |
| 7 | 📋 Queue | player_show_queue | เปิด Queue Page |
| 8 | 🔊 Volume | player_volume | เปิด Volume Modal |
| 9 | 🔀 Shuffle | player_shuffle | สลับ Upcoming เท่านั้น |

### 49.2 Other interactive component IDs

| UI | custom_id | หน้าที่ |
|---|---|---|
| Queue previous | queue_previous_page | เปลี่ยนหน้า Queue ย้อนกลับ |
| Queue page indicator | queue_page | แสดงหน้าปัจจุบัน ไม่กดใช้งาน |
| Queue next | queue_next_page | เปลี่ยนหน้า Queue ไปข้างหน้า |
| Queue done search | queue_done_search | ค้นหาเพลงหลัง Queue จบ |
| Queue done stop | queue_done_stop | ปิด/หยุดจากหน้า Queue จบ |
| Search result select | search_result_select | เลือกผลค้นหา |
| Search add all | search_result_add_all | เพิ่มผลค้นหาทั้งหมด |
| Search close | search_result_close | ปิดผลค้นหา |
| Radio single | youtube_radio_single | เล่นเพลงเดียว |
| Radio playlist | youtube_radio_playlist | โหลดเพลงจาก Radio |
| Playlist count | playlist_count_{amount} | เลือกจำนวนเพลงที่จะโหลด โดยแสดงเฉพาะ 5/10/20/30 ที่ไม่เกินจำนวนเพลงที่ค้นพบ |
| Playlist Add All | playlist_count_all | เพิ่มเพลงทั้งหมดที่ค้นพบ โดยจำนวนที่ค้นพบถูกจำกัดสูงสุด 50 เพลง |
| Volume modal | volume_modal | Volume modal |
| Volume input | volume_modal_input | ช่องกรอก 0-100 |
| Search modal | search_modal | Search modal |
| Search input | search_modal_input | ช่องค้นหา |

ID ที่ผูกกับ state/handler ต้องไม่ถูกเปลี่ยนชื่อโดยไม่มีการอัปเดตทุกจุดที่อ้างอิง

### 49.3A Playlist count button logic

Playlist metadata is fetched with a hard maximum of 50 tracks (MAX_PLAYLIST_FETCH).

The count-selection View must follow these rules:
- Numeric buttons are only shown when the discovered track count reaches that amount.
- Numeric choices are limited to 5, 10, 20, and 30.
- The old numeric 50 button is removed.
- Add All (N) is always shown when at least 1 track was discovered.
- Add All (N) means add all tracks actually discovered by the fetch, where N is at most 50.
- If fewer than 5 tracks are discovered, show only Add All (N).
- Examples:
  - 3 tracks → [Add All (3)]
  - 8 tracks → [5] [Add All (8)]
  - 17 tracks → [5] [10] [Add All (17)]
  - 35 tracks → [5] [10] [20] [30] [Add All (35)]
  - 120-track playlist with fetch capped at 50 → [5] [10] [20] [30] [Add All (50)]
- Add All does not mean unlimited playlist loading; it means all tracks successfully discovered in the capped fetch.
- The displayed N must use the actual discovered track list length, not an external playlist total.
- Selecting a count must not change the current track; it only appends the selected tracks to Queue.
- After adding, Main Player and open Queue Page must refresh.
- Repeated clicks while the operation is busy must not add the same batch twice.

### 49.3 Main Player scenarios

**Scenario A — เริ่มเพลง #1**
- Previous disabled
- Pause แสดง ⏸️
- Next enabled ถ้ามีเพลงถัดไป
- Shuffle disabled ถ้า Upcoming < 2
- Repeat แสดง 🔁 / off
- Queue เปิดได้ถ้ามี Queue

**Scenario B — Pause → Resume**
- กด ⏸ → VoiceClient paused
- Player เปลี่ยนปุ่มเป็น ▶️
- กด ▶️ → VoiceClient resumed
- Player เปลี่ยนกลับเป็น ⏸️
- Queue state ไม่เปลี่ยน

**Scenario C — Previous → Next**
- Current #22 → Previous → #21
- Queue entry #22 ยังอยู่ตำแหน่งเดิม
- Next → #22
- ห้าม append/duplicate #22
- Player และ Queue marker ต้องตรงกัน

**Scenario D — Natural End**
- #30 จบ → #31
- History/Upcoming คำนวณใหม่
- marker ย้าย #30 → #31
- Player + Queue refresh พร้อมกัน

**Scenario E — Skip ระหว่าง playback**
- A → กด Skip → B
- callback ของ A ที่มาช้าต้องไม่เปลี่ยน Current
- Player/Queue ต้องยังแสดง B

**Scenario F — Shuffle**
- Current คงเดิม
- เฉพาะ Upcoming ถูก shuffle
- History ไม่เปลี่ยน
- logical queue numbers ไม่เปลี่ยน
- Player + Queue refresh
- ถ้ามี Upcoming < 2 เพลง ให้ reject

**Scenario G — Repeat**
- กดครั้งที่ 1: off → track, emoji 🔂
- กดครั้งที่ 2: track → queue, emoji 🔁
- กดครั้งที่ 3: queue → off
- Track repeat ต้องใช้ Queue entry เดิม ไม่ duplicate
- Queue repeat เมื่อถึงท้ายให้กลับไปเพลงแรกที่ยังอยู่ใน memory

**Scenario H — Volume**
- กรอก 0 → source volume 0%
- กรอก 50 → source volume 50%
- กรอก 100 → source volume 100%
- ค่าผิดช่วงต้อง reject
- Player ต้อง refresh ค่าที่แสดง

**Scenario I — Stop**
- หยุด playback
- clear Queue/History/Current/session state
- ลบ/ปิด Queue views ที่ติดตามอยู่
- Player ต้องไม่สามารถควบคุม session เก่าได้

**Scenario J — Queue pagination**
- 100 เพลง → 20 เพลงต่อหน้า
- Previous/Next เปลี่ยนเฉพาะ page
- ไม่เปลี่ยน Current
- page ที่มี Current ต้องแสดง marker ที่ Current

**Scenario K — Search**
- Player Search → Modal
- submit → search result UI
- เลือกผล → เพิ่ม/เริ่มเล่นตาม flow ที่ implementation กำหนด
- เพิ่มเพลงแล้ว source Queue ต้องเปลี่ยนก่อน refresh UI

**Scenario L — Queue Page**
- กด 📋 → เปิด Queue Page แยก
- เปิดที่ page ของ Current เมื่อจำเป็น
- pagination ไม่เปลี่ยน Current
- เมื่อเพลงเปลี่ยน Queue Page ที่เปิดอยู่ต้อง refresh marker และเลขคิว

**Scenario M — Queue Done**
- Queue หมด → แสดง Queue Done controls
- 🔍 เปิด Search ได้
- ⏹ หยุด/ออกได้
- session เก่าต้องไม่ถูกนำกลับมาโดย callback เก่า

**Scenario N — External Voice Disconnect**
กรณี Bot ถูกผู้ใช้เตะ/ตัดออกจาก Voice Channel หรือ Voice connection หลุดโดยไม่ได้เกิดจากปุ่ม Stop

ต้อง:
1. ตรวจว่า Bot ไม่ได้อยู่ใน Voice Channel เดิมแล้ว
2. invalidate Player session เดิม
3. invalidate playback generation เดิม
4. invalidate background Playlist fetch ของ session เดิม
5. clear Queue / History / Current และ session state ตาม `clear_guild()`
6. ล้าง Queue/Search UI references ที่ติดตาม session เดิม
7. Player เดิมต้องไม่สามารถเปลี่ยน playback, Queue หรือ Current ได้อีก
8. ห้ามเรียก `vc.stop()` หรือ `vc.disconnect()` ซ้ำกับ Voice Client ที่หลุดไปแล้ว
9. หากมี `/play` ใหม่ ต้องสร้าง Player session และ playback generation ใหม่
10. callback/task จาก session เดิมต้องไม่มีสิทธิ์เติมเพลง เปลี่ยน Current หรือสร้าง playback ใหม่ใน session ใหม่

ผลลัพธ์ที่ต้องได้:
- Queue = empty
- History = empty
- Current = none
- Upcoming = empty
- Player session เดิม = invalid
- background Playlist task เดิม = invalid
- playback callback เดิม = ignored

### 49.4 Acceptance rule

ก่อน merge PR ต้องตรวจอย่างน้อย:
1. ทุก custom_id มี handler หรือเป็น indicator ที่ตั้งใจให้กดไม่ได้
2. Player มี 9 controls ตาม contract
3. ทุก state-changing button refresh source state และ UI ที่เกี่ยวข้อง
4. Previous/Next ไม่สร้าง duplicate
5. Pause/Resume แสดงสถานะจริง
6. Shuffle/Repeat ไม่หายจาก View
7. Stop clear session จริง
8. Queue pagination ไม่เปลี่ยน Current
9. Search/Volume modal เปิดและตอบ interaction ได้
10. callback เก่าห้ามเขียน state ทับ Current ใหม่


## 50. Development Progress Log

### 2026-10-08 — Main Player control order + YouTube resilience

สิ่งที่เพิ่ม/แก้ล่าสุด:

1. **Main Player button order**
   - Row 0: `[ ⏮ ] [ ⏸ ] [ ⏭ ] [ ⏹ ] [ 🔁 ]`
   - Row 1: `[ 🔍 ] [ 📋 ] [ 🔊 ] [ 🔀 ]`
   - แก้ลำดับการประกาศปุ่มใน `PlayerView` ให้ตรงกับ UI contract
   - `custom_id` ของทุกปุ่มยังคงเดิม เพื่อไม่ให้ handler/state mapping แตก

2. **YouTube anti-bot resilience**
   - `fetch_track()` เป็นจุดกลางสำหรับการดึงเพลงเดี่ยว
   - หมายเหตุ: ข้อนี้เป็นแนวทางเดิมก่อนปรับปรุง; implementation ปัจจุบันไม่ retry หลัง anti-bot และใช้ cooldown 600 วินาทีตามหัวข้อ Current implementation ด้านล่าง
   - ใช้ `YoutubeDL` instance ใหม่ต่อ attempt
   - ไม่เพิ่ม delay ในกรณีที่ fetch สำเร็จตั้งแต่ครั้งแรก
   - ไม่ใช้ cookies/browser session

3. **Playlist / playback flow ที่มีอยู่**
   - เพลงแรกของ Playlist ถูก fetch และเริ่มเล่นก่อน
   - เพลงที่เหลือถูก fetch ต่อใน background
   - จำกัด background concurrency ที่ `PLAYLIST_FETCH_CONCURRENCY = 5`
   - มี generation guard ป้องกันงานเก่าจากการเติมเพลงหลัง Stop/disconnect

4. **เอกสารและ acceptance contract**
   - Main Player มี control order ที่ระบุชัดเจน
   - ต้องตรวจว่า Player และ Queue ยังคง refresh/sync หลัง state-changing actions
   - PR ยังอยู่ในขั้นทดสอบ และยังไม่ merge เข้า `main`

### Current implementation checkpoint

- Branch: `fix/clean-player-title`
- ล่าสุด: Main Player modern UI applied in `slashcommands.py`
- UI contract in this section is the source of truth for the approved layout
- ต้องทดสอบจริงหลัง pull PR ก่อน merge `main`


## 51. Player / Queue UI Contract — 2026-10-08

> **UI decision:** layout below is the approved Main Player visual contract. Future UI changes must preserve this hierarchy and divider length unless the contract is explicitly revised.

### Main Player visual hierarchy

Main Player must prioritize the currently playing track and playback progress. History and Next are supporting information and must remain visually compact.

Approved layout:

```
🎵 **NOW PLAYING**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎧 **เพลงที่กำลังเล่น**
    *Artist Name • YouTube*

    **1:24** ━━━━━━━━━━━━━━━━━━━━━ **3:42**

    👤 SEA_Beach          🔊 10%
    🔀 Shuffle            🔁 Repeat

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📚 *History*
01 🎵 เพลงก่อนหน้า 1                  `3:21`
02 ♫ เพลงก่อนหน้า 2                  `4:05`
03 🎵 เพลงก่อนหน้า 3                  `2:58`

⏭️ *Next*
04 🎵 เพลงถัดไป 1                     `4:12`
05 🎵 เพลงถัดไป 2                     `3:36`
06 🎵 เพลงถัดไป 3                     `5:01`

⏳ กำลังโหลดเพลงเพิ่มเติม • 10 / 20
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
กำลังเล่น **#4 จาก 20 เพลง**
```

Rules:
- `🎵 NOW PLAYING` is the primary heading and should receive the strongest visual emphasis available in Discord Markdown.
- The current track title is bold: `🎧 **เพลงที่กำลังเล่น**`.
- Artist/source is secondary and italic: `*Artist Name • YouTube*`.
- Playback time/progress is visually emphasized: `**elapsed** ━━━━━━━━━━━━━━━━━━━━━ **duration**`.
- Requester and volume are normal metadata: `👤 SEA_Beach` and `🔊 10%`.
- Shuffle and Repeat are displayed as status labels: `🔀 Shuffle` and `🔁 Repeat`.
- `📚 *History*` and `⏭️ *Next*` are intentionally compact and must not visually compete with Now Playing.
- Do not use large/bold History or Next headings.
- Main Player shows up to 10 History items and 5 Upcoming items.
- History and Upcoming list rows remain normal-weight text; duration is rendered as inline code.
- The visual progress bar is part of the approved layout. The sample uses `**1:24**` as an illustration; runtime elapsed time must come from playback state when an elapsed-time tracker is available. Until then, the implementation may use `0:00` as a placeholder and must not invent elapsed values.
- The standard divider is exactly `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━` (49 characters).
- Thumbnail remains supported through the current track's thumbnail and may be shown on the Embed when available.
- Interactive playback controls are Discord buttons below the Embed, not text inside the Embed.

### Conditional Queue sections and exact spacing

The Main Player keeps the approved layout and divider positions, but each optional section is rendered only when that section has data.

The rules are:

**Condition 1 — History + Next + Loading**
- Render `📚 *History*` and its rows.
- Render `⏭️ *Next*` and its rows.
- Render `⏳ กำลังโหลดเพลงเพิ่มเติม • X / N`.
- Keep the same vertical spacing shown in the approved full layout.

**Condition 2 — No History + Next + Loading**
- Do not render the History heading or any empty History area.
- Render `⏭️ *Next*` at the same position where the first available queue section begins.
- Render Loading below Next with the same spacing as the approved layout.

**Condition 3 — No History + No Next + Loading**
- Do not render History or Next headings/empty areas.
- Render Loading immediately after the Main Player divider, with no extra blank line before it.
- Keep the final divider and queue-position line in their approved positions.

General rule:
- An absent section is removed as a complete section; do not leave an empty heading, placeholder, or artificial blank block.
- When multiple sections are present, preserve the exact ordering and spacing from the approved layout: History → Next → Loading.
- If History, Next, and Loading are all absent, do not append a second closing divider; the divider immediately below the Main Player is the only divider shown at the bottom.
- If Loading is rendered, preserve the closing divider after the loading line.
- The 49-character divider remains unchanged.

### Main Player buttons

The actual clickable Discord buttons remain below the Embed in exactly two rows:

```
[ ⏮ ] [ ⏸ ] [ ⏭ ] [ ⏹ ] [ 🔁 ]
[ 🔍 ] [ 📋 ] [ 🔊 ] [ 🔀 ]
```

The button contract remains:
- Row 0: `⏮ Previous`, `⏸/▶️ Pause/Resume`, `⏭ Next`, `⏹ Stop`, `🔁/🔂 Repeat`.
- Row 1: `🔍 Search`, `📋 Queue`, `🔊 Volume`, `🔀 Shuffle`.
- These are real `discord.ui.Button` components and must not be represented as fake text controls in the Embed.
- Button state must continue to reflect the actual playback/Queue state.

### Deferred voice connection for YouTube Radio / Mix

- Opening the YouTube Radio / Mix choice screen must not connect or move the bot into a voice channel.
- Choosing `เล่นเพลงนี้เท่านั้น` may connect the bot because the user has made the playback choice.
- Choosing `โหลดเพลงจาก Radio` may fetch flat playlist metadata to build the count picker, but must not connect or move the bot yet.
- For the Radio / Mix playlist path, `PlaylistCountView` starts with no VoiceClient. Only after the requester selects a count does the background selection processor connect/move the bot and begin queueing the selected tracks.
- If the requester is no longer in a voice channel, show the existing voice-channel error and do not start playback.

### Playlist count picker responsiveness

- After the requester chooses a song count, acknowledge the interaction and close the `📋 เลือกจำนวนเพลง` picker before starting YouTube track extraction.
- Start the selected playlist processing as a background task so the picker does not remain visible while the first playable track is being fetched.
- The first playable track still starts as soon as its extraction succeeds; remaining tracks continue through the existing background/concurrent fetch path.
- Report selection-processing errors through an ephemeral follow-up. Do not silently lose exceptions from the background task.

### Queue divider for a single track

- When the Queue contains exactly one track, render the top divider but omit the bottom `QUEUE_DIVIDER` line.
- With two or more tracks, preserve the bottom divider as usual.

### Queue page navigation reliability

- Queue Previous (`◀`) and Next (`▶`) must update the component message through `interaction.response.edit_message(...)` in the same interaction; do not defer and then call `edit_original_response(...)`.
- Recalculate the page count and clamp the current page, then rebuild/synchronize the controls before rendering the embed for that exact page.
- If the queue has only one page, show no navigation controls at all, including no page indicator, and omit `Page 1 / 1` from the embed footer too.
- If the queue has multiple pages, show `◀`, a disabled page indicator, and `▶`. On the first page, disable only `◀`; on middle pages, keep both arrows enabled; on the final page, disable only `▶`.
- Queue refreshes caused by playback or queue changes must preserve each user's current page while recalculating the page count and button disabled states.

### Active-only Shuffle / Repeat status and toggle behavior

- The Main Player status area shows `🔀 Shuffle` only while Shuffle is enabled; show its state as `เปิด`.
- The Main Player shows Repeat only while Repeat is active. `track` is displayed as `🔁 Repeat: วนเพลงนี้`; `queue` is displayed as `🔁 Repeat: วน Queue`.
- When both are off, neither status label is rendered. Keep the requester/volume row and the approved divider spacing intact.
- Shuffle is a two-state toggle: off → on (shuffle Upcoming once) and on → off (stop treating Shuffle as active). Pressing while on must not reshuffle Upcoming again.
- Queue page navigation must call `_sync_buttons()` after changing `page` and before editing the message, so Previous/Next disabled states match the newly displayed page.

### Queue page layout

Queue page uses one consistent item icon:
- Current track: `▶️` and the entire row is bold.
- All other tracks: `🎵`.
- The current marker moves with the real `now_playing_idx`.
- There must be exactly one current marker.
- Current marker and logical queue number must stay synchronized with Main Player.

Example:

```
📋 QUEUE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
01 🎵 เพลงก่อนหน้า                    `3:21`
02 🎵 เพลงก่อนหน้า                    `4:05`
03 **▶️ เพลงที่กำลังเล่น                `3:42`**
04 🎵 เพลงถัดไป                       `4:12`
05 🎵 เพลงถัดไป                       `3:36`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Page 1 / 1  •  5 songs  •  กำลังเล่น #3
```

### Playlist loading status

Background playlist fetch must not block playback of the first playable track.

- `PLAYLIST_FETCH_CONCURRENCY = 1` (process-wide); remaining entries wait 5 seconds before each extraction.
- Main Player shows loading status while additional playlist tracks are being fetched:
  `⏳ กำลังโหลดเพลงเพิ่มเติม • X / N`
- `X` means the number of playlist entries whose fetch attempt has completed, including skipped/failed entries.
- Update the Player loading status every 5 completed entries.
- When the background fetch finishes, refresh Player and Queue immediately.
- When loading finishes, remove the temporary loading line.
- If a playlist has only one playable entry and no background work remains, no loading line is shown.
- Loading state is session-aware and must not leak into a newer Player session.

### Queue-add UI synchronization

When a track is appended to an already-playing Queue:
1. Update Queue state.
2. Refresh Main Player so Next/Previous/Repeat/Shuffle/Queue button states are recalculated.
3. Refresh open Queue views.
4. Do not manually edit only the Embed while leaving the View stale.

This specifically prevents the Next button from remaining disabled after adding another track.

### UI icon contract

| Area | Icon / style |
|---|---|
| Main Player header | 🎵 + bold |
| Current track title | 🎧 + bold |
| Artist / source | italic |
| Progress time | bold |
| History header | 📚 + italic |
| History item | 🎵 |
| Next header | ⏭️ + italic |
| Next item | 🎵 |
| Queue current item | ▶️ + bold |
| Queue other item | 🎵 |
| Loading | ⏳ |
| Actual controls | Discord buttons inside the Player Components V2 Container |


## 34. Player Components V2

Player หลักใช้ `discord.ui.LayoutView` และ `discord.ui.Container` จาก discord.py 2.6 ขึ้นไป
แทน Embed + View แบบเดิม เพื่อให้รายการเพลงและปุ่มอยู่ในกรอบ Component เดียวกัน

ลำดับภายใน Container:
1. Now Playing: ชื่อเพลง, ศิลปิน/แหล่งที่มา, ผู้ขอเพลง, ภาพปกเมื่อมี
2. แถบเวลาที่แสดงใน Player
3. แถบ Volume 10 ช่อง โดยไม่แสดงตัวเลขเปอร์เซ็นต์
4. History 3 เพลงล่าสุด (แต่ระบบ Previous ยังเก็บย้อนหลังสูงสุด 10 เพลง)
5. Up Next 3 เพลงถัดไป (ไม่จำกัดจำนวนเพลงจริงใน Queue)
6. ปุ่มควบคุมใน Container: Previous, Pause/Resume, Next, Stop, Repeat, Shuffle, Search, Queue, Volume

### Link behavior

- ชื่อเพลงปัจจุบัน, History และ Up Next เป็น Markdown link เมื่อมี URL หน้าเพลงจริงจาก yt-dlp metadata.
- ห้ามใช้ stream URL ของ FFmpeg เป็นลิงก์ที่ผู้ใช้กด เพราะอาจเป็น URL ชั่วคราว ไม่ใช่หน้าเพลง.
- หากไม่พบ URL หน้าเพลงจริง ให้แสดงชื่อเพลงเป็นข้อความธรรมดา.
- ไม่เพิ่มไอคอนลิงก์ภายนอกต่อท้ายชื่อเพลง.
- Discord client อาจควบคุมสี/เส้นใต้ของ hyperlink เอง จึงห้ามรับประกันหน้าตาเหมือน mockup ทุกแพลตฟอร์ม.

### Compatibility and message lifecycle

- `requirements.txt` ต้องกำหนด `discord.py>=2.6.0,<3.0`.
- Components V2 message ห้ามส่ง `embed=` หรือ `content=` ควบคู่กับ `LayoutView`; เนื้อหาต้องอยู่ใน TextDisplay/Container.
- เมื่อ Stop หรือ external disconnect ให้ลบ Player V2 message แล้วส่งข้อความสรุปแบบ legacy แยกต่างหาก เพราะ V2 message ไม่สามารถเปลี่ยนกลับไปใช้ Embed เดิมในข้อความเดียวกันได้.
- ต้องทดสอบ callback ของทุกปุ่ม, Previous/Next, queue refresh, repost debounce, natural end, stop และ external disconnect บน Discord จริงก่อน merge.
- Player คำนวณ elapsed time จาก playback clock และ refresh หน้าจอทุก 10 วินาทีขณะเล่น รวมถึง refresh ทันทีหลังเปลี่ยนเพลง/seek/pause/resume.


### Current implementation update — 2026-10-09

This section supersedes older UI notes above where they conflict with the implemented interaction.

#### Main Player controls

- Row 1: Previous, seek back 10 seconds, Pause/Resume, seek forward 10 seconds, Next.
- The seek buttons use the requested custom emoji IDs: 1455985625097306142 for back 10 seconds and 1455985627714551839 for forward 10 seconds. There is no additional −10s / +10s label.
- When an emoji is not available to the bot in the current server and external emoji usage is not permitted, the button falls back to a Unicode seek symbol rather than risking the entire Player message failing to send.
- Row 2: Shuffle, Stop, Repeat. Ordinary controls use Secondary styling; Stop remains Danger styling.
- Normal shortcut row contains Search, Queue and Volume. Playlist-count and Radio/Mix are opened by their existing commands/flows, not extra Player shortcuts.
- The History section shows the latest three previous tracks in newest-first order. Previous-track navigation still uses the up-to-10 History entries in Queue state.

#### Queue page

- The Player's Queue shortcut opens a separate ephemeral Components V2 Queue view with 10 tracks per page.
- Each row displays the logical Queue number, title link when a safe YouTube page URL exists, requester and total duration. The current track is marked ▶️ กำลังเล่น; no elapsed time is displayed in Queue rows.
- Each available thumbnail appears as the Section accessory beside its row. Discord Components V2 places a Section accessory on the right; it does not provide a built-in left-side accessory option.
- Pager buttons are Secondary gray ◀ / ▶ only. The page indicator is disabled and informational. Previous is disabled on the first page; Next is disabled on the last page. Both arrows are disabled for an empty Queue or a Queue of 10 or fewer tracks.
- The Queue has its own message so the Player stays intact and the Queue can include up to 10 thumbnail sections without exceeding Discord Components V2's 40-component total limit.
- A user's open Queue view is refreshed when the Player state refreshes. Navigation edits the ephemeral Queue message and preserves that user's page.

#### Playlist count and Radio/Mix

- Playlist choices are 5, 10, 20 and 30 whenever the found count is greater than or equal to that choice. Add All always uses the actual available count capped at 50. The cap applies to selected tracks, not just the button label.
- The playlist-count submenu has no “กลับเครื่องเล่น” button.
- Radio/Mix presents ▶️ เล่นเพลงนี้ and 📋 โหลดเพลงจาก Mix side-by-side, then ✖️ ยกเลิก on the second row. Cancel does not add tracks.
- The Queue submenu has no Back button; its pager uses arrow-only controls.

#### Playback clock and button-state synchronization

- Player elapsed time and its proportional bar refresh every 10 seconds while audio is playing.
- Starting a track from a single-song flow or playlist starts the refresh task immediately after the Player message is assigned.
- Switching tracks resets the displayed playback clock immediately. Pause freezes elapsed time; resume restarts its clock; seeking updates the displayed time immediately.
- The player refresh function rebuilds the layout before syncing button styles/disabled states, because layout rebuild creates new Button objects. This order is a correctness requirement.
- The open Queue page is updated along with the Player when the refresh loop runs.

#### YouTube anti-bot response

The error “Sign in to confirm you're not a bot” is a YouTube access restriction; yt-dlp cannot guarantee that every video or IP/session will be allowed. The following mitigations reduce avoidable repeated requests but do not bypass YouTube authorization:

1. Update yt-dlp with the default extra regularly. This installs the matching yt-dlp-ejs challenge scripts. A supported JavaScript runtime is also required for current YouTube challenge solving; Deno is the recommended runtime. Reference: https://github.com/yt-dlp/yt-dlp/wiki/EJS

   On Windows, install Deno in PowerShell using `winget install DenoLand.Deno`, then close/reopen the terminal and verify with `deno --version`. After updating the project dependencies, restart the bot process so the Python process picks up the installed runtime. Official instructions: https://docs.deno.com/runtime/getting_started/installation/
2. Do not rotate to an alternative YouTube player client after a detected challenge. Stop the blocked request immediately, open the shared cooldown, and avoid additional requests that could worsen rate limiting.
3. Use yt-dlp's sleep_interval_requests pacing (default 1.0 second between internal extraction requests, configurable with YTDLP_SLEEP_REQUESTS from 0 to 10 seconds). Keep retry counts low, cap simultaneous playlist fetches at four globally across guilds, and apply a 5-second delay before fetching each remaining entry. The first playable track is still started immediately.
4. If a challenge or a recognized rate-limit response such as HTTP 429 hits a YouTube search, playlist listing or track fetch, open a shared 600-second (10-minute) circuit breaker. During that window, new YouTube requests fail fast; do not rotate alternate player clients, retry the blocked request, or search replacement titles for a blocked playlist entry.
5. Playlist entries are fetched sequentially through a semaphore shared by all guilds, with a 5-second pause between entries. If the circuit breaker opens, remaining items are counted as cooldown skips without launching more yt-dlp workers. Worker exceptions are caught and counted so cleanup can finish.
6. Raw yt-dlp logger output is suppressed so terminal errors cannot append to the carriage-return progress line. The worker still records a short cause in the guild log.
7. Optional YTDLP_COOKIES_FILE can point to a Netscape/Mozilla-format cookies file on the host. Mount/configure this file outside the repository. Never commit it, print its contents, or paste it into logs. Cookies are sensitive login credentials and can expire. Reference: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp

#### Regression checklist before merge

- [ ] This branch intentionally has no automated tests/workflow. Run `python -m py_compile slashcommands.py bot.py`, then execute the manual regression scenarios in Section 51 before merging.
- [ ] With 0, 1, 10, 11, 20 and 21 tracks, check Queue pager states and page content.
- [ ] With 4, 5, 11, 25, 48 and 80 found tracks, check count buttons and the 50-track cap.
- [ ] Verify both custom seek emoji render in the target Discord server and seek exactly 10 seconds. If the server cannot use the emojis, verify Unicode fallback.
- [ ] Verify elapsed time increments every 10 seconds, pauses, resumes, seeks and resets on Next/Previous.
- [ ] Simulate anti-bot and HTTP 429 errors in track fetch, playlist listing and search; verify the 600-second circuit breaker, no retry/client rotation after a block, and no title-search fallback during a block.
- [ ] Verify `YTDLP_SLEEP_REQUESTS` is safely parsed and remaining playlist entries are fetched one at a time with a 5-second gap through the process-wide semaphore.
- [ ] Trigger an anti-bot challenge midway through a playlist and verify remaining entries are marked as cooldown skips, without more extraction attempts or misleading per-track block counts.
- [ ] Force one background fetch worker to raise unexpectedly and verify the loading status is cleared at completion.
- [ ] Test all button callbacks in Discord, including Radio/Mix, playlist count, Queue pagination, Stop, and external voice disconnect.


## 51. Playback-flow regression scenarios (manual Discord verification)

The following scenarios are the acceptance matrix for the playback-race fixes. They are documented for manual verification; the repository's automated `tests/` directory and GitHub Actions workflow were intentionally removed, so these cases must not be described as automated or live-tested.

| Scenario | Action | Expected result |
|---|---|---|
| F1 — initial playback | `/play` one track, then add two more | First track starts; two tracks append to Upcoming; no track is skipped if VoiceClient briefly reports idle during its completion callback. |
| F2 — Previous then Next | With at least three tracks, move from B to A, then Next | Current returns to B; no duplicate queue item is appended and logical numbers do not change. |
| F3 — previous at oldest retained history | Advance beyond 11 played tracks, then repeatedly Previous | Last 10 prior tracks are retained; earlier trimmed tracks are absent from both player History and queue state; an extra Previous is rejected. |
| F4 — completion callback races Previous | At song end, click Previous while the completion callback is pending | A callback with an old generation/session/index is ignored after it obtains the Queue lock; it cannot advance over the selected track. |
| F5 — rapid navigation | Quickly alternate Previous, Skip, and seek while audio is transitioning | Relative navigation is resolved from the current index under lock; stale callbacks cannot change Current after a newer source starts. |
| F6 — seek boundary | Seek backward near 0, forward near the end, and seek as a track ends | Position clamps to valid bounds; seek does not append a track or allow its replaced source callback to advance Queue. |
| F7 — Repeat Track | Enable Repeat Track and allow a song to finish | The same Queue entry plays again; Queue length and logical numbers remain unchanged. |
| F8 — Repeat Queue | Enable Repeat Queue and finish the last retained/upcoming entry | Playback returns to the first entry still present in Queue; trimmed history is not resurrected. |
| F9 — stop / disconnect race | Stop or disconnect as a callback is pending, then start a new `/play` | Old session callbacks are ignored; the old 5-minute Queue-End timer cannot disconnect the new playback. |
| F10 — Queue-End then new song | Let Queue finish and issue `/play` before the 5-minute idle timeout | New Player session stays connected and the previous Queue-End task exits without deleting the new Player. |
| F11 — Shuffle message mapping | Add several tracks, enable Shuffle, and let the shuffled tracks play | Shuffle touches Upcoming only; Queue-add messages remain mapped to their original track and no unrelated message is deleted. |
| F12 — playlist count choices | Try discovered counts 3, 8, 17, 35, and 120 | Choices follow 3: Add All (3); 8: 5 + Add All (8); 17: 5/10 + Add All (17); 35: 5/10/20/30 + Add All (35); 120 is capped to 50. |
| F13 — playlist pacing | Load a long playlist | First playable track starts promptly; remaining extraction uses one process-wide worker with a five-second gap; anti-bot cooldown stops further extraction attempts. |
| F14 — interaction error | Trigger a stale Player or a handler exception | User receives an expired-player/error response where possible; the error is logged and is not silently mistaken for a successful transition. |
| F15 — Queue pager | Open Queue, page forward/back, then advance playback while the view remains open | Page boundaries stay valid; paging does not change Current; refreshed page reflects the current marker and logical queue number. |

**Manual verification status:** pending a live run in the target Discord server. Static source review and the code-level race guards alone do not prove Discord/VoiceClient runtime behavior.

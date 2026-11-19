# gta6-trophy-rarity-forecast-01-v4

[![make.fun](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/competitions/gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/zesty-pika-87/gta6-trophy-rarity-forecast-01-v4/rank.svg)](https://make.fun/competitions/gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/zesty-pika-87/gta6-trophy-rarity-forecast-01-v4/reward.svg)](https://make.fun/competitions/gta6-trophy-rarity-forecast-01)

Forecast rarity GTA 6 di Exophase: untuk tiap trofi, perkirakan persen pemain
yang sudah meraihnya pada waktu target, sebagai 5 kuantil (5%, 25%, 50%, 75%,
95%). Satu file `main.py`, Python 3.13 stdlib saja, deterministik, tanpa
network dan tanpa wall clock.

## Metode

- **History sebagai tangga state.** Read berurutan yang identik (jumlah
  pemain sama dan rates sama persis) digabung jadi satu state.
- **Regime-drop.** State awal dibuang selama state berikutnya jumlah
  pemainnya lebih dari 3x state pertama — refresh besar membuat state lama
  tidak relevan.
- **Fit tren berjangkar (anchored).** Di jendela 48 jam terakhir (atau semua
  state bila kurang dari 3), regresi linier terbobot pada log-rate terhadap
  log-waktu sejak rilis, bobot recency meluruh dengan half-life 24 jam.
  Slope didampingi 0,6x dan **tidak** ditarik ke rata-rata (SHRINK 0:
  slope riser vs grind trophy memang beda populasi). Median BUKAN dari
  intercept fit di titik rilis, melainkan rate terakhir yang digerakkan
  slope terdampingi sejauh gap log-waktu ke target — jadi damping
  men-temper tren *dari* rate terakhir, dan damp ke 0 sama dengan
  persistence. Bentuk intercept lama merusak backtest pertama di mana fit
  aktif (B4: mean slope -0,777 menarik faller naik dan riser turun; Beat
  Cal 10x terlalu rendah). Median dijepit ke [0,1x, 10x] rate terakhir.
  Tanpa fit yang sah, median = rate terakhir (persistence).
- **Koreksi satu arah.** Refresh mendilusi rates: median digeser turun
  0,3 logit, spread bawah dilebarkan 1,3x, spread atas dipersempit 0,35x.
- **Spread logit.** Akar dari jumlah kuadrat: suku horizon
  (0,5 x akar hari), sigma refresh (0,7 bila ada fit, 1,0 bila tanpa fit),
  dan residu fit (dibatasi 1,0). Kuantil memakai z -1,645 / -0,674 / 0 /
  0,674 / 1,645 di sekitar logit median.
- **Trofi 100%.** Bila rate terakhir >= 99,99, forecast datar
  [100, 100, 100, 100, 100].

## Cek lokal (v4 — anchored + shift -0,3 + spread 1,3x/0,35x)

| Case | Loss |
| --- | --- |
| p00 vs dev actual | 0,607 |
| b1 vs b1 actual (tanpa refresh) | 0,242 |
| b2 vs b2 actual (refresh 81 mnt) | 0,358 |
| b3 vs b2 actual (refresh 538 mnt) | 0,476 |
| b4 vs b4 actual (tanpa refresh, fit aktif) | 0,239 |
| b5 vs b5 actual (refresh 589 mnt) | 0,476 |

Kontrol: v3 (REC) 0,648 / 0,255 / 0,395 / 0,504 / 0,695 / 0,503 dan BASE
0,918. v4 mengalahkan v3 di semua 7 case; kemenangan terbesar di B4
(0,695 -> 0,239), satu-satunya backtest di mana fit tren aktif.

## Cara menjalankan

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Membaca tiap `*.in` dari `MAKEFUN_INPUT_DIR` dan menulis `<stem>.out` ke
`MAKEFUN_OUTPUT_DIR`, satu baris JSON ringkas per case.

---
Entered on [make.fun](https://make.fun/?ref=1aaz13gb)

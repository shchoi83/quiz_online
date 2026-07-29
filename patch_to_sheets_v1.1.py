# -*- coding: utf-8 -*-
"""
patch_to_sheets.py  v1.1
퀴즈 HTML 전체에 Google Sheets 이중 저장(dual-write) 코드를 일괄 적용.

사용법:
    quiz_online 폴더에 이 파일을 두고

    python patch_to_sheets.py          <- 미리보기 (파일 변경 없음)
    python patch_to_sheets.py --apply  <- 실제 적용

백업은 저장소 바깥(../quiz_online_bak_YYYYMMDD_HHMM/)에 저장되므로
GitHub에 함께 push되지 않습니다.
"""

import re
import sys
import shutil
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════
# 설정
# ══════════════════════════════════════
GS_ENDPOINT = "https://script.google.com/macros/s/AKfycbzay6SAgvghcFDisFxk32UjxOpSg7aa7k85PrlqIYYE6fuCrAZAsbb_yyQI6N06y0zzrA/exec"

TARGET_DIR = Path(".")          # quiz_online 폴더
PATTERN    = "*-quiz-*.html"
BACKUP     = True               # 저장소 바깥에 원본 백업

# 테스트 페이지는 이미 자체 코드가 있으므로 제외
EXCLUDE = {"test-quiz-sheets.html"}

# ══════════════════════════════════════
# 치환 1: 설정 블록에 엔드포인트 추가
# ══════════════════════════════════════
OLD_CONF = 'const AT_SCORES  = "scores";'

NEW_CONF = '''const AT_SCORES  = "scores";

// ── Google Sheets 웹앱 (주 저장소) ──
const GS_ENDPOINT = "%s";
const DUAL_WRITE  = true;   // 전환 완료 후 false 로 바꾸면 Airtable 전송 중단''' % GS_ENDPOINT

# ══════════════════════════════════════
# 치환 2: saveScore 함수 전체 교체
# ══════════════════════════════════════
OLD_SAVE_RE = re.compile(
    r"// ═+\n// Airtable 저장\n// ═+\n"
    r"async function saveScore\(score, total\) \{.*?\n\}\n",
    re.DOTALL
)

NEW_SAVE = '''// ══════════════════════════════════════
// 점수 저장 (Google Sheets 주 저장소 + Airtable 병행)
// ══════════════════════════════════════
function buildPayload(score, total) {
  return {
    '이름': userName,
    '학교': userSchool,
    'phone_last4': userPhone,
    '날짜': new Date().toISOString().split('T')[0],
    '과목코드': QUIZ_META.subjectCode,
    '과목명': QUIZ_META.subjectName,
    '대단원번호': QUIZ_META.mainNo,
    '대단원명': QUIZ_META.mainName,
    '중단원번호': QUIZ_META.subNo,
    '중단원명': QUIZ_META.subName,
    '섹션번호': QUIZ_META.sectionNo,
    '섹션명': QUIZ_META.sectionName,
    '총점': score,
    '총문항수': total,
    '풀이시간': fmtTime(elapsedSec)
  };
}

async function saveToSheets(payload) {
  // Content-Type을 text/plain 으로 보내야 preflight(OPTIONS)가 생기지 않음.
  // Apps Script 웹앱은 OPTIONS를 처리하지 못하므로 이 설정이 필수.
  const res = await fetch(GS_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain;charset=utf-8' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.ok !== true) throw new Error(data.error || 'sheets rejected');
  return data;
}

async function saveToAirtable(payload) {
  const res = await fetch(`https://api.airtable.com/v0/${AT_BASE}/${AT_SCORES}`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${AT_TOKEN}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ fields: payload })
  });
  if (!res.ok) throw new Error('airtable ' + res.status);
  return true;
}

async function saveScore(score, total) {
  toast('점수 저장 중...');
  const payload = buildPayload(score, total);

  const jobs = [saveToSheets(payload)];
  if (DUAL_WRITE) jobs.push(saveToAirtable(payload));

  const results = await Promise.allSettled(jobs);
  const sheetsOk   = results[0].status === 'fulfilled';
  const airtableOk = DUAL_WRITE ? results[1].status === 'fulfilled' : false;

  if (!sheetsOk) console.warn('[Sheets 저장 실패]', results[0].reason);
  if (DUAL_WRITE && !airtableOk) console.warn('[Airtable 저장 실패]', results[1].reason);

  if (sheetsOk || airtableOk) {
    toast('✓ 점수가 저장되었습니다.');
  } else {
    toast('저장 실패 — 네트워크를 확인해주세요.');
  }
}
'''


def main():
    apply = '--apply' in sys.argv

    files = sorted(f for f in TARGET_DIR.glob(PATTERN) if f.name not in EXCLUDE)
    if not files:
        print('대상 파일을 찾지 못했습니다.')
        print('현재 폴더: %s' % TARGET_DIR.resolve())
        print('quiz_online 폴더 안에서 실행하고 있는지 확인하세요.')
        return

    print('대상 파일 %d개 발견\n' % len(files))

    # 백업 (실제 적용할 때만)
    bak_dir = None
    if apply and BACKUP:
        stamp = datetime.now().strftime('%Y%m%d_%H%M')
        bak_dir = TARGET_DIR.resolve().parent / ('quiz_online_bak_' + stamp)
        bak_dir.mkdir(exist_ok=True)
        for f in files:
            shutil.copy2(f, bak_dir / f.name)
        print('원본 백업: %s\n' % bak_dir)

    ok, skipped = 0, []

    for f in files:
        src = f.read_text(encoding='utf-8')

        if 'GS_ENDPOINT' in src:
            skipped.append((f.name, '이미 패치됨'))
            continue
        if OLD_CONF not in src:
            skipped.append((f.name, '설정 블록 불일치'))
            continue
        if not OLD_SAVE_RE.search(src):
            skipped.append((f.name, 'saveScore 블록 불일치'))
            continue

        out = src.replace(OLD_CONF, NEW_CONF, 1)
        out = OLD_SAVE_RE.sub(lambda m: NEW_SAVE, out, count=1)

        if apply:
            f.write_text(out, encoding='utf-8')
        ok += 1
        print('  o %s' % f.name)

    print()
    print('%s: %d개 성공 / %d개 건너뜀' % ('적용' if apply else '미리보기', ok, len(skipped)))
    for name, why in skipped:
        print('  - %s  (%s)' % (name, why))

    if ok and not apply:
        print('\n실제 적용:  python patch_to_sheets.py --apply')
    if ok and apply:
        print('\n완료. 브라우저로 퀴즈 하나를 열어 저장을 테스트한 뒤 push하세요.')


if __name__ == '__main__':
    main()

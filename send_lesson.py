#!/usr/bin/env python3
"""Daily Georgian citizenship-test lesson -> Telegram topic: lesson text + native quiz polls.

Course = lessons.json (30 days from start, synced with ПЛАН_гражданство_и_тест.md), questions = bank.json.
Usage: send_lesson.py [--day N] [--quiz-only] [--dry]
"""
import argparse
import html
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = "/home/claudebot/.env"
CHAT_ID = os.environ.get("GEO_CHAT_ID", "-1003984716519")  # work group
THREAD_ID = os.environ.get("GEO_THREAD_ID", "31")  # topic «Грузинский язык»
PROGRESS = Path.home() / "obsidian-vault/Georgian Language/Тест на гражданство/Прогресс.md"
POLL_Q_MAX, POLL_EXPL_MAX = 300, 200  # Telegram quiz limits
SEND_PAUSE = 3.2  # group limit ~20 messages/min
ABC_QUIZ_N = 10
REVIEW_N = 3

LESSONS = json.loads((ROOT / "lessons.json").read_text(encoding="utf-8"))
BANK = json.loads((ROOT / "bank.json").read_text(encoding="utf-8"))
BY_ID = {q["id"]: q for q in BANK}
DRY = False


def get_token() -> str:
    m = re.search(r'^TELEGRAM_TOKEN="?([^"\n]+)', Path(ENV_FILE).read_text(), re.M)
    if not m:
        raise SystemExit("no TELEGRAM_TOKEN in .env")
    return m.group(1)


def api(method: str, **params) -> dict:
    if DRY:
        print(method, {k: (v[:70] + "…" if isinstance(v, str) and len(v) > 70 else v) for k, v in params.items()})
        return {"ok": True}
    params = {"chat_id": CHAT_ID, "message_thread_id": THREAD_ID, **params}
    data = urllib.parse.urlencode(
        {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict, bool)) else v for k, v in params.items()}
    ).encode()
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    for _ in range(5):
        try:
            with urllib.request.urlopen(url, data=data, timeout=20) as r:
                time.sleep(SEND_PAUSE)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
            wait = body.get("parameters", {}).get("retry_after")
            if e.code == 429 and wait:
                time.sleep(wait + 1)
                continue
            raise SystemExit(f"{method} failed: {e.code} {body.get('description')}")
    raise SystemExit(f"{method}: too many retries")


def msg(text: str) -> None:
    api("sendMessage", text=text, parse_mode="HTML", disable_web_page_preview=True)


def quiz(question: str, opts: list[str], correct: int, expl: str, rng: random.Random) -> None:
    order = list(range(len(opts)))
    rng.shuffle(order)  # the real exam may reorder options: learn the answer, not its position
    api("sendPoll", question=question[:POLL_Q_MAX], options=[opts[i] for i in order], type="quiz",
        correct_option_id=order.index(correct), explanation=expl[:POLL_EXPL_MAX], is_anonymous=True)


def send_question(q: dict, rng: random.Random, tag: str = "") -> None:
    head = f"{tag}{q['id']}. {q['q']}"
    full = f"{head}\n\n{q['ctx']}" if q["ctx"] else head
    both = f"{q['ru']}\n{q['why']}"
    expl = both if len(both) <= POLL_EXPL_MAX else q["why"]
    if len(full) <= POLL_Q_MAX:
        quiz(full, q["opts"], q["a"], expl, rng)
        return
    # long reading text: send it as a message with the translation under a spoiler, then the poll
    msg(f"<b>{html.escape(tag + q['id'])}</b>\n\n{html.escape(q['ctx'])}\n\n"
        f"Перевод: <tg-spoiler>{html.escape(q['ru'])}</tg-spoiler>")
    quiz(head, q["opts"], q["a"], q["why"], rng)


def abc_quizzes(letters: list[str], new: list[str], n: int, rng: random.Random) -> None:
    sound = dict(LESSONS["alphabet"])
    pick = new + rng.sample([c for c in letters if c not in new], max(0, min(n, len(letters)) - len(new)))
    rng.shuffle(pick)
    for c in pick:
        wrong = rng.sample([s for ch, s in LESSONS["alphabet"] if ch != c], 3)
        opts = [sound[c]] + wrong
        quiz(f"Как читается буква  {c} ?", opts, 0, f"{c} = {sound[c]}", rng)


def send_explanations_for_day(day: dict, rng: random.Random) -> None:
    """Send key patterns and rules from today's questions before the quizzes."""
    if not day.get("ids"):
        return
    questions = [BY_ID[i] for i in day["ids"] if i in BY_ID]
    if not questions:
        return

    # Extract unique explanations and patterns
    explanations = []
    for q in questions[:3]:  # sample first 3 for patterns
        if q.get("why"):
            explanations.append(q["why"])

    if explanations:
        # Group and deduplicate
        expl_text = "\n".join([f"• {e}" for e in explanations[:2]])  # top 2 patterns
        msg(f"<b>Правило дня:</b>\n\n{expl_text}\n\n<i>Отвечай внизу на каждый квиз. После ответа — полное объяснение.</i>")


def covered_before(n: int) -> list[str]:
    return [i for d in LESSONS["days"] if d["n"] < n for i in d.get("ids", [])]


def send_day(day: dict, quiz_only: bool) -> None:
    n = day["n"]
    rng = random.Random(f"{LESSONS['start']}-{n}")
    if not quiz_only:
        msg(day["text"])
    if "abc" in day:
        prev = LESSONS["days"][n - 2].get("abc", []) if n > 1 else []
        new = [c for c in day["abc"] if c not in prev] if n <= 5 else []
        msg("<b>Квизы: буквы</b>")
        abc_quizzes(day["abc"], new, day.get("abc_n", ABC_QUIZ_N), rng)
    if day.get("ids"):
        # Send explanations BEFORE quizzes so user learns first
        send_explanations_for_day(day, rng)
        msg("<b>Квизы: вопросы теста</b>")
        for i in day["ids"]:
            send_question(BY_ID[i], rng)
    if day.get("review"):
        pool = covered_before(n)
        if pool:
            msg("<b>Повтор пройденного</b>")
            for i in rng.sample(pool, min(day["review"], len(pool))):
                send_question(BY_ID[i], rng, "Повтор · ")
    if "mix" in day:
        secs = day["mix"]["sec"]
        pool = [q for q in BANK if not secs or q["sec"] in secs]
        for q in rng.sample(pool, day["mix"]["k"]):
            send_question(q, rng)
    for e in range(day.get("exams", 1) if "exam" in day else 0):
        msg(f"<b>Экзамен {e + 1}</b> · 10 вопросов · засеки 20 минут")
        for k, q in enumerate(rng.sample(BANK, day["exam"]), 1):
            send_question(q, rng, f"{k}/10 · ")
    msg("Готово на сегодня. Ответь сюда: задание, счёт квизов и ошибки — проверю.")


def pick_day(override: int | None) -> dict:
    days = LESSONS["days"]
    if override:
        return days[override - 1]
    n = (date.today() - date.fromisoformat(LESSONS["start"])).days + 1
    if n < 1:
        raise SystemExit("course not started")
    # after day 30: keep exam days on repeat until the test is passed
    return days[n - 1] if n <= len(days) else days[22 + (n - 23) % 8]


def log(day: dict) -> None:
    if DRY:
        return
    if not PROGRESS.exists():
        PROGRESS.write_text("# Прогресс\n\n| Дата | День | Тема | Счёт |\n|---|---|---|---|\n", encoding="utf-8")
    title = re.sub(r"<[^>]+>", "", day["text"].split("\n")[0])
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(f"| {date.today().isoformat()} | {day['n']} | {title} | — |\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int)
    ap.add_argument("--quiz-only", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    DRY = a.dry
    TOKEN = "" if DRY else get_token()
    d = pick_day(a.day)
    send_day(d, a.quiz_only)
    log(d)
    print(f"sent day {d['n']}")

"""
営業開始ストーリーを毎朝自動投稿するプログラム(古着 shii 用)

- 日曜・祝日は SUNDAY_IMAGE(phonto 5)を投稿
- それ以外の日は、残りの画像を番号順(番号なしが最初)にローテーション
- 休み:第2・第4火曜 / 12月31日〜1月5日 / holidays.txt に書いた日付
- 投稿に成功した日だけ順番を進める(state.json に記録)
"""

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import re
import urllib.request
from pathlib import Path

import jpholiday

ROOT = Path(__file__).resolve().parent
IMAGES_DIR = ROOT / "images"
STATE_FILE = ROOT / "state.json"
HOLIDAYS_FILE = ROOT / "holidays.txt"

API = "https://graph.instagram.com/v23.0"
JST = datetime.timezone(datetime.timedelta(hours=9))

# 日曜・祝日に使う画像(images フォルダ内のファイル名)
SUNDAY_IMAGE = "phonto 5.JPG"


# ---------- 休みの判定 ----------

def load_extra_holidays():
    days = set()
    if HOLIDAYS_FILE.exists():
        for line in HOLIDAYS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            try:
                days.add(datetime.date.fromisoformat(line))
            except ValueError:
                print(f"holidays.txt の書き方が違う行をスキップ: {line}")
    return days


def holiday_reason(d, extra):
    # 第2・第4火曜(火曜=weekday 1)
    if d.weekday() == 1:
        nth = (d.day - 1) // 7 + 1
        if nth in (2, 4):
            return f"第{nth}火曜の定休日"
    # 年末年始 12/31〜1/5
    if (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 5):
        return "年末年始のお休み"
    if d in extra:
        return "臨時休業(holidays.txt)"
    return None


# ---------- 画像の選び方 ----------

def is_sunday_or_holiday(d):
    return d.weekday() == 6 or jpholiday.is_holiday(d)


def number_in_name(path):
    # "phonto.JPG" → 1、"phonto 2.JPG" → 2 のように番号を取り出す(番号なしは1扱い)
    m = re.search(r"(\d+)\s*$", path.stem)
    return int(m.group(1)) if m else 1


def find_images():
    return [p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg")]


# ---------- 状態 ----------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"next": 0, "last_posted": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- Instagram API ----------

def api(method, path, params):
    url = f"{API}/{path}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Instagram APIエラー ({e.code}): {body}") from None


def post_story(token, image_url):
    me = api("GET", "me", {"fields": "user_id,username", "access_token": token})
    user_id = me.get("user_id") or me.get("id")
    print(f"アカウント: @{me.get('username')}")

    container = api("POST", f"{user_id}/media", {
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": token,
    })
    cid = container["id"]

    # 準備完了を待つ(最大約2分)
    for _ in range(24):
        status = api("GET", cid, {"fields": "status_code", "access_token": token}).get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError("Instagram側で画像の処理に失敗しました(JPEG・9:16か確認してください)")
        time.sleep(5)

    result = api("POST", f"{user_id}/media_publish", {"creation_id": cid, "access_token": token})
    print(f"投稿しました(media id: {result.get('id')})")


# ---------- メイン ----------

def main():
    token = os.environ.get("IG_TOKEN")
    if not token:
        sys.exit("IG_TOKEN が設定されていません(GitHubのSecretsを確認してください)")

    force = os.environ.get("FORCE") == "true"   # 手動テスト用:休みでも投稿する
    today = datetime.datetime.now(JST).date()
    state = load_state()

    if not force:
        reason = holiday_reason(today, load_extra_holidays())
        if reason:
            print(f"{today} は{reason}なので投稿しません")
            return
        if state.get("last_posted") == str(today):
            print(f"{today} はもう投稿済みなのでスキップします")
            return

    images = find_images()
    if not images:
        sys.exit("images フォルダにJPEG画像がありません")

    sunday = next((p for p in images if p.name.lower() == SUNDAY_IMAGE.lower()), None)
    rotation = sorted((p for p in images if p != sunday), key=lambda p: (number_in_name(p), p.name))

    use_sunday = is_sunday_or_holiday(today) and sunday is not None
    if use_sunday:
        image = sunday
        print(f"今日は日曜・祝日なので {image.name} を使います")
    else:
        if is_sunday_or_holiday(today):
            print(f"注意:{SUNDAY_IMAGE} が見つからないので、通常のローテーションで投稿します")
        idx = state.get("next", 0) % len(rotation)
        image = rotation[idx]
        print(f"今日の画像: {image.name}({idx + 1}/{len(rotation)}枚目)")

    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    image_url = f"https://raw.githubusercontent.com/{repo}/{sha}/images/{urllib.parse.quote(image.name)}"
    post_story(token, image_url)

    if not use_sunday:
        state["next"] = (idx + 1) % len(rotation)
    state["last_posted"] = str(today)
    save_state(state)


if __name__ == "__main__":
    main()

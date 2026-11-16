#!/usr/bin/env python3
"""Analyse the UDC awards DB and generate insights.

Reads udc_awards.json (produced by udc_fetch.py) and writes:
    stats.json    all aggregations as machine-readable data
    insights.md   human-readable insight report, generated from the data

Usage:
    python3 udc_analyze.py [--dir DIR]
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

TOP_AWARDS = {"金賞", "最優秀賞"}
SECOND_AWARDS = {"銀賞", "優秀賞"}
THIRD_AWARDS = {"銅賞", "奨励賞"}

CLUSTERS = {
    "文化・アーカイブ": {
        "theme": "生活・文化・地域アーカイブ",
        "keywords": [
            "アーカイブ", "歴史", "文化財", "伝統", "祭", "踊り", "民謡",
            "カルタ", "かるた", "地名", "写真", "昔", "石造物", "供養塔",
            "藩日記", "遺産", "方言", "民話", "昔話", "レトロ", "遺跡",
        ],
    },
    "観光": {
        "theme": "産業・観光",
        "keywords": [
            "観光", "旅", "ツアー", "巡", "めぐり", "ロゲ", "まち歩き",
            "お出かけ", "スポット", "ガイド", "周遊", "訪問",
        ],
    },
    "ゲーム": {
        "theme": None,
        "keywords": [
            "ゲーム", "クイズ", "カルタ", "かるた", "RPG", "謎解き",
            "脱出", "ロゲ", "ボードゲーム", "遊び", "プレイ",
        ],
    },
    "可視化": {
        "theme": None,
        "keywords": [
            "可視化", "見える化", "マップ", "地図", "3D", "３Ｄ",
            "デジタルツイン", "ビューア", "Viewer", "ダッシュボード",
            "シミュレーション", "VR", "AR",
        ],
    },
}


def tier(award):
    if award in TOP_AWARDS:
        return "金賞級（金賞/最優秀賞）"
    if award in SECOND_AWARDS:
        return "銀賞級（銀賞/優秀賞）"
    if award in THIRD_AWARDS:
        return "銅賞級（銅賞/奨励賞）"
    return "特別賞・その他"


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def cluster_of(rec):
    hits = []
    for name, spec in CLUSTERS.items():
        if spec["theme"] and rec["theme"] == spec["theme"]:
            hits.append(name)
            continue
        if any(k in rec["title"] for k in spec["keywords"]):
            hits.append(name)
    return hits


def top_examples(recs, limit=5):
    order = {"金賞級（金賞/最優秀賞）": 0, "銀賞級（銀賞/優秀賞）": 1,
             "銅賞級（銅賞/奨励賞）": 2, "特別賞・その他": 3}
    ranked = sorted(recs, key=lambda r: (order[tier(r["award"])], -r["year"]))
    return ranked[:limit]


def fmt_counter(counter, total, limit=None):
    items = counter.most_common(limit)
    return ", ".join(f"{k} {v}件（{pct(v, total)}%）" for k, v in items if k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir", default=str(Path.home() / "workspace/projects/udc-awards")
    )
    args = ap.parse_args()
    d = Path(args.dir)
    recs = json.loads((d / "udc_awards.json").read_text(encoding="utf-8"))
    validation = {}
    vpath = d / "validation.json"
    if vpath.exists():
        validation = json.loads(vpath.read_text(encoding="utf-8"))

    years = sorted({r["year"] for r in recs})
    by_year = Counter(r["year"] for r in recs)
    themed = [r for r in recs if r["theme"]]
    typed = [r for r in recs if r["type"]]
    by_theme = Counter(r["theme"] for r in themed)
    by_type = Counter(r["type"] for r in typed)
    by_tier = Counter(tier(r["award"]) for r in recs)
    by_division = Counter(r["division"] for r in recs)

    # period comparison (themes/types only exist for later years)
    early = [r for r in recs if r["year"] <= 2019]
    late = [r for r in recs if r["year"] >= 2020]
    late_themes = Counter(r["theme"] for r in late if r["theme"])
    type_by_year = defaultdict(Counter)
    for r in typed:
        type_by_year[r["year"]][r["type"]] += 1

    # clusters
    cluster_stats = {}
    for name in CLUSTERS:
        members = [r for r in recs if name in cluster_of(r)]
        cluster_stats[name] = {
            "count": len(members),
            "share_pct": pct(len(members), len(recs)),
            "tiers": dict(Counter(tier(r["award"]) for r in members)),
            "types": dict(Counter(r["type"] for r in members if r["type"])),
            "themes": dict(Counter(r["theme"] for r in members if r["theme"])),
            "years": dict(Counter(r["year"] for r in members)),
            "examples": [
                {
                    "year": r["year"], "award": r["award"],
                    "theme": r["theme"], "type": r["type"],
                    "title": r["title"], "team": r["team"],
                }
                for r in top_examples(members)
            ],
        }

    # gold-rate of visualisation works vs overall
    vis = cluster_stats["可視化"]
    vis_top = vis["tiers"].get("金賞級（金賞/最優秀賞）", 0)
    all_top = by_tier.get("金賞級（金賞/最優秀賞）", 0)

    stats = {
        "total_records": len(recs),
        "years": years,
        "by_year": {str(k): v for k, v in sorted(by_year.items())},
        "by_theme": dict(by_theme),
        "by_type": dict(by_type),
        "by_tier": dict(by_tier),
        "by_division": dict(by_division),
        "themed_records": len(themed),
        "typed_records": len(typed),
        "late_period_themes": dict(late_themes),
        "type_by_year": {
            str(y): dict(c) for y, c in sorted(type_by_year.items())
        },
        "clusters": cluster_stats,
        "validation": validation,
    }
    (d / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # ------------------------------------------------------------ insights.md
    L = []
    L.append("# UDC受賞作品 インサイト分析")
    L.append("")
    L.append(
        f"自動生成（udc_analyze.py）。対象：公式審査結果ページ "
        f"{years[0]}–{years[-1]} の受賞作 {len(recs)} 件。"
        "テーマ・タイプは公式表に存在する年のみ集計"
        f"（テーマ {len(themed)} 件／タイプ {len(typed)} 件）。"
    )
    L.append("")
    L.append("## 規模と推移")
    L.append("")
    L.append(
        "年別件数：" + ", ".join(f"{y}年 {by_year[y]}件" for y in years) + "。"
    )
    L.append(
        f"賞の内訳：{fmt_counter(by_tier, len(recs))}。"
        "特別賞の枠（実行委員会特別賞・GTFS賞・BODIK賞など）が後年ほど増え、"
        "本賞以外にも拾われる経路が広がっている。"
    )
    L.append("")
    L.append("## テーマ別（2020年以降、公式テーマ分類のある受賞作）")
    L.append("")
    L.append(fmt_counter(late_themes, sum(late_themes.values())) + "。")
    culture_theme_n = late_themes.get("生活・文化・地域アーカイブ", 0)
    culture_theme_top = sum(
        1 for r in late
        if r["theme"] == "生活・文化・地域アーカイブ"
        and tier(r["award"]) == "金賞級（金賞/最優秀賞）"
    )
    L.append(
        f"「生活・文化・地域アーカイブ」は {culture_theme_n} 件で、"
        f"うち金賞級が {culture_theme_top} 件。"
        "文化・アーカイブは端数テーマではなく、金賞を取る本線の一つ。"
    )
    L.append("")
    L.append("## タイプ別")
    L.append("")
    L.append(fmt_counter(by_type, len(typed)) + "。")
    app_share = pct(by_type.get("アプリケーション", 0), len(typed))
    L.append(
        f"アプリケーションが {app_share}% と最大勢力である一方、"
        "アクティビティ（活動そのもの）も継続的に受賞しており、"
        "「動くもの＋地域での活動」の複合型が強い。"
    )
    L.append("")
    for name, cs in cluster_stats.items():
        L.append(f"## クラスタ：{name}（{cs['count']}件、全体の{cs['share_pct']}%）")
        L.append("")
        L.append(f"- 賞の内訳：{fmt_counter(Counter(cs['tiers']), cs['count'])}")
        if cs["types"]:
            L.append(
                f"- タイプ：{fmt_counter(Counter(cs['types']), sum(cs['types'].values()))}"
            )
        if cs["themes"]:
            L.append(
                f"- テーマ：{fmt_counter(Counter(cs['themes']), sum(cs['themes'].values()), 3)}"
            )
        L.append("- 代表例：")
        for e in cs["examples"]:
            meta = "／".join(x for x in [e["theme"], e["type"]] if x)
            L.append(
                f"  - {e['year']} {e['award']}「{e['title']}」"
                + (f"（{meta}）" if meta else "")
                + (f" — {e['team']}" if e["team"] else "")
            )
        L.append("")

    L.append("## 読み取れる共通点")
    L.append("")
    L.append(
        f"1. **可視化は金賞級の常連**：タイトルに可視化系キーワードを含む作品は "
        f"{vis['count']} 件あり、金賞級 {vis_top} 件"
        f"（全体の金賞級 {all_top} 件の {pct(vis_top, all_top)}%）。"
        "「見えなかったものを見せる」は最有力の勝ち筋。"
    )
    game = cluster_stats["ゲーム"]
    game_top = game["tiers"].get("金賞級（金賞/最優秀賞）", 0)
    game_second = game["tiers"].get("銀賞級（銀賞/優秀賞）", 0)
    L.append(
        f"2. **ゲーム形式は単独でも届く**：ゲーム系 {game['count']} 件中、"
        f"金賞級 {game_top} 件・銀賞級 {game_second} 件。"
        "船内脱出ゲームのように、同じチーム・同じ型が複数年にわたり受賞する例もある。"
    )
    cul = cluster_stats["文化・アーカイブ"]
    cul_top = cul["tiers"].get("金賞級（金賞/最優秀賞）", 0)
    L.append(
        f"3. **記録・アーカイブの公共性が評価される**：文化・アーカイブ系 "
        f"{cul['count']} 件中、金賞級 {cul_top} 件。"
        "2019年金賞「石造物3Dアーカイブ」が典型で、"
        "「消えゆくものを記録する」こと自体が受賞理由になる。"
    )
    tour = cluster_stats["観光"]
    L.append(
        f"4. **観光は継続型が強い**：観光系 {tour['count']} 件。"
        "飛騨高山の観光まちづくりは2022年ビジネス部門最優秀賞→2024年銀賞→"
        "2025年実行委員会特別賞と、年をまたいで磨く型が評価されている。"
    )
    L.append(
        "5. **市民参加でデータを補う仕組み**：2025年銀賞「クイズでつなぐ石川のまち」"
        "のように、遊びながら地域データの不足を埋める設計は近年の評価軸。"
    )
    L.append("")
    L.append("## 「旅する民謡ゲーム」への示唆")
    L.append("")
    L.append(
        f"- テーマは「生活・文化・地域アーカイブ」が本線（2020年以降 {culture_theme_n} 件、"
        f"金賞級 {culture_theme_top} 件）。民謡・民舞はこの枠に正確に収まる。"
    )
    L.append(
        "- 最重要の先例は2019年銀賞「すその阿波踊り『にわか連』可視化サービス "
        "にわかにGO」。民舞×可視化は実証済みの組み合わせで、本応募でも前面に出す価値がある。"
    )
    L.append(
        "- ゲーム（クイズ）×市民参加でデータを集める構造は2025年銀賞"
        "「クイズでつなぐ石川のまち」と同型。違いは「民謡の分布と継承者データ」という"
        "新しいデータ層を持つこと。ここを差別化点として明記すべき。"
    )
    L.append(
        "- タイプは「アプリケーション」を軸に、現地イベント等の「アクティビティ」"
        "要素を添えると、受賞作の複合型パターンに一致する。"
    )
    L.append(
        "- 特別賞の枠が多いので、本賞に届かなくても実行委員会特別賞などの"
        "受け皿がある。継続参加（複数年で磨く）自体が評価される土壌がある。"
    )
    L.append("")
    L.append("## 方法と限界")
    L.append("")
    L.append(
        "- 出典は公式サイト urbandata-challenge.jp の各年審査結果ページのみ。"
        "2013・2014年は現行公式サイトに審査結果ページが無く欠落。"
    )
    L.append(
        "- テーマ・タイプの公式分類は2020年以降の表にのみ存在する"
        "（2015–2018年は部門名からタイプのみ復元、2019年は分類なし）。"
    )
    L.append(
        "- 2023–2025年の公式表にはチーム名欄が無いため team は空欄。"
    )
    L.append(
        "- クラスタ抽出はタイトルへのキーワード一致による機械的な分類で、"
        "境界事例を含む。件数は傾向把握のための目安。"
    )
    L.append("")
    (d / "insights.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote stats.json and insights.md ({len(recs)} records)")


if __name__ == "__main__":
    main()

# udc-analysis

アーバンデータチャレンジ（UDC）受賞作の分析を、毎年作り直して積む場所。

- data/ — 受賞作DBの正本（2015–2025、298件、公式審査結果ページから取得）
- analysis/ — その年の分析。insights-2015-2025.md は全期間の基本分析、focus-YYYY-*.md は年ごとの絞り込み分析
- scripts/ — 取得と集計のスクリプト（udc_fetch.py / udc_analyze.py）。運用の詳細は Muse のスキル udc_awards が正本

来年も同じ形で作る：新しい審査結果が出たら DB に年を足し、その年の重点分野で focus を1本書く。

位置づけ：この repo は企画にとっての**外発的な要因**（受賞の力学という外の環境）。対になる内発的な要因は bonsai/neo-minyo-land で、企画はこの2つでドライブされる。

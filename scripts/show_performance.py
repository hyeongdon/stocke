"""실행 중인 서버의 /performance/stats 결과 출력."""
import json
import urllib.request

URL = "http://127.0.0.1:8000/performance/stats?source=auto&seed=10000000"


def main():
    d = json.load(urllib.request.urlopen(URL, timeout=40))
    print("pipeline      :", d.get("pipeline"))
    print("data_source   :", d.get("data_source"), "| pipeline:", d.get("pipeline"))
    print("period        :", d.get("period"))
    print("trade_count   :", d.get("trade_count"))
    print("net_pnl       :", f"{d.get('net_pnl', 0):,}")
    print("gross_pnl     :", f"{d.get('gross_pnl', 0):,}")
    print("total_cost    :", f"{d.get('total_cost', 0):,}")
    print("win_rate      :", d.get("win_rate"), f"% ({d.get('wins')}W {d.get('losses')}L)")
    print("profit_factor :", d.get("profit_factor"), "| payoff:", d.get("payoff"))
    print("mdd           :", f"{d.get('mdd', 0):,}")
    print("best / worst  :", f"{d.get('best', 0):,} / {d.get('worst', 0):,}")
    print("trading_days  :", d.get("trading_days"))
    print()
    print("일별 손익(최근):")
    for r in d.get("daily", [])[:6]:
        print(f"  {r['date']}  청산 {r['count']:>2}  승 {r['wins']:>2}  손익 {r['pnl']:>14,}")


if __name__ == "__main__":
    main()

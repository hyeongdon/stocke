"""계산 방식 비교"""
buy_price = 443000
current_price = 431000
quantity = 1
buy_commission = 1550  # 실제 매수 수수료

# 총 투자비용
total_investment = buy_price * quantity + buy_commission

print("=" * 60)
print("계산 방식 비교")
print("=" * 60)
print()
print(f"매입금액: {buy_price * quantity:,}원")
print(f"매수 수수료: +{buy_commission:,}원")
print(f"총 투자비용: {total_investment:,}원")
print()

# 사용자 제공 계산
print("사용자 제공 계산:")
print(f"  현재가: {current_price:,}원")
print(f"  예상 매도 수수료: -647원 (431,000 x 0.015% = 647원)")
print(f"  예상 거래세: -862원 (431,000 x 0.2% = 862원)")
print(f"  순수입: 429,491원")
print(f"  손익: -15,059원")
print(f"  수익률: -3.59%")
print()

# 제 계산
sell_commission_rate = 0.00015
tax_rate = 0.002
sell_commission = current_price * quantity * sell_commission_rate
tax = current_price * quantity * tax_rate
expected_net_proceeds = current_price * quantity - sell_commission - tax
profit_loss = expected_net_proceeds - total_investment
profit_rate = (profit_loss / total_investment) * 100

print("제 계산:")
print(f"  현재가: {current_price:,}원")
print(f"  예상 매도 수수료: -{sell_commission:,.0f}원 ({current_price * quantity * sell_commission_rate:,.2f}원)")
print(f"  예상 거래세: -{tax:,.0f}원")
print(f"  순수입: {expected_net_proceeds:,.0f}원")
print(f"  손익: {profit_loss:+,.0f}원")
print(f"  수익률: {profit_rate:+.2f}%")
print()

# 차이 분석
print("차이 분석:")
print(f"  매도 수수료 차이: {647 - sell_commission:,.0f}원")
print(f"  순수입 차이: {429491 - expected_net_proceeds:,.0f}원")
print(f"  손익 차이: {abs(-15059 - profit_loss):,.0f}원")
print()

# 역산: 사용자 계산에서 매도 수수료가 647원이 나온 이유
print("역산 분석:")
print(f"  431,000 × 0.015% = {431000 * 0.00015:,.2f}원")
print(f"  사용자 계산: 647원")
print(f"  차이: {647 - (431000 * 0.00015):,.0f}원")
print()

# 혹시 매도 수수료가 다른 방식일까?
# 647 / 431000 = ?
print(f"  647 / 431,000 = {647 / 431000 * 100:.4f}%")
print(f"  혹시 매도 수수료율이 다를까?")
print()

# 사용자 계산을 역산
user_net_proceeds = 429491
user_profit = -15059
user_total_investment = user_net_proceeds - user_profit
print(f"사용자 계산 역산:")
print(f"  순수입: {user_net_proceeds:,}원")
print(f"  손익: {user_profit:,}원")
print(f"  역산 총 투자비용: {user_total_investment:,}원")
print(f"  실제 총 투자비용: {total_investment:,}원")
print(f"  차이: {abs(user_total_investment - total_investment):,.0f}원")


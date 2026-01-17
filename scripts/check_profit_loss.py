"""평가손익 계산 검증"""
buy_price = 443000
current_price = 431000
quantity = 1
actual_buy_amount = 447049  # 키움 API의 pur_amt (수수료 포함)

# 기존 방식 (매수가 기준)
old_profit_loss = (current_price - buy_price) * quantity
old_rate = (current_price - buy_price) / buy_price * 100

# 키움 방식 (실제 매입금액 기준)
actual_buy_price = actual_buy_amount / quantity
new_profit_loss = (current_price - actual_buy_price) * quantity
new_rate = (current_price - actual_buy_price) / actual_buy_price * 100

print("=" * 60)
print("평가손익 계산 비교")
print("=" * 60)
print()
print(f"매수가: {buy_price:,}원")
print(f"현재가: {current_price:,}원")
print(f"수량: {quantity}주")
print(f"실제 매입금액(수수료 포함): {actual_buy_amount:,}원")
print(f"실제 매입가: {actual_buy_price:,.0f}원")
print()
print("기존 방식 (매수가 기준):")
print(f"  평가손익: {old_profit_loss:+,}원")
print(f"  수익률: {old_rate:+.2f}%")
print()
print("키움 방식 (실제 매입금액 기준):")
print(f"  평가손익: {new_profit_loss:+,}원")
print(f"  수익률: {new_rate:+.2f}%")
print()
print("키움 평가손익: 15,911원")
print(f"계산된 평가손익: {new_profit_loss:+,}원")
print(f"차이: {abs(15911 - new_profit_loss):,}원")


"""매도 수수료 계산 확인"""
current_price = 431000
sell_commission = 647

# 역산
rate = sell_commission / current_price
rate_percent = rate * 100

print("=" * 60)
print("매도 수수료 역산")
print("=" * 60)
print()
print(f"현재가: {current_price:,}원")
print(f"매도 수수료: {sell_commission:,}원")
print()
print(f"계산된 수수료율: {rate_percent:.4f}%")
print(f"계산된 수수료율 (소수): {rate:.6f}")
print()
print("비교:")
print(f"  0.015% = {0.00015 * 100:.4f}%")
print(f"  0.15% = {0.0015 * 100:.4f}%")
print(f"  계산된 비율 = {rate_percent:.4f}%")
print()
print(f"431,000 × 0.015% = {431000 * 0.00015:,.2f}원")
print(f"431,000 × 0.15% = {431000 * 0.0015:,.2f}원")
print(f"431,000 × {rate:.6f} = {sell_commission:,}원")


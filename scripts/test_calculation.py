"""계산 공식 테스트"""
import math
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 사용자 제공 실제 값
STOCKS = [
    {"name": "한국단자", "current_price": 74800, "actual_buy_amount": 447600, "quantity": 6, "target": -2827},
    {"name": "현대모비스", "current_price": 431000, "actual_buy_amount": 443000, "quantity": 1, "target": -15911},
    {"name": "대한항공", "current_price": 24300, "actual_buy_amount": 497000, "quantity": 20, "target": -15402},
    {"name": "삼성화재", "current_price": 486500, "actual_buy_amount": 487500, "quantity": 1, "target": -5372},
    {"name": "현대건설", "current_price": 104300, "actual_buy_amount": 411000, "quantity": 4, "target": 2477},
]

print("=" * 60)
print("모의투자 공식 테스트 (제세금 0.557%)")
print("=" * 60)
print()

for stock in STOCKS:
    name = stock["name"]
    current_price = stock["current_price"]
    actual_buy_amount = stock["actual_buy_amount"]
    quantity = stock["quantity"]
    target = stock["target"]
    
    # 모의투자 공식
    sell_fee = math.floor(current_price * quantity * 0.0035)  # 0.35%
    tax = math.floor(current_price * quantity * 0.00557)  # 0.557%
    
    evaluation_amount = current_price * quantity - sell_fee - tax
    profit_loss = evaluation_amount - actual_buy_amount
    profit_rate = (profit_loss / actual_buy_amount) * 100
    
    diff = abs(profit_loss - target)
    
    print(f"{name}:")
    print(f"  현재가 x 수량: {current_price:,}원 x {quantity}주 = {current_price * quantity:,}원")
    print(f"  매도 수수료 (0.35%): {sell_fee:,}원")
    print(f"  제세금 (0.557%): {tax:,}원")
    print(f"  평가금액: {evaluation_amount:,}원")
    print(f"  실제매입금액: {actual_buy_amount:,}원")
    print(f"  계산 손익: {profit_loss:,}원 ({profit_rate:.2f}%)")
    print(f"  목표 손익: {target:,}원")
    print(f"  차이: {diff:,}원")
    if diff <= 100:
        print(f"  [OK] 정확함")
    else:
        print(f"  [WARN] 차이 있음")
    print()

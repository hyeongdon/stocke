"""
디버그 모드 테스트 스크립트
서버가 실행 중인 상태에서 실행하세요
"""
import requests
import time
import json

BASE_URL = "http://localhost:8000"

def print_response(response, title):
    """응답 출력"""
    print(f"\n{'='*60}")
    print(f"📌 {title}")
    print(f"{'='*60}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print(f"상태 코드: {response.status_code}")
    print()

def main():
    print("🔍 디버그 모드 테스트 스크립트")
    print("="*60)
    
    # 1. 디버그 모드 활성화
    print("\n1️⃣  디버그 모드 활성화...")
    response = requests.post(f"{BASE_URL}/debug/enable")
    print_response(response, "디버그 모드 활성화")
    
    # 2. 상태 확인
    print("\n2️⃣  디버그 상태 확인...")
    response = requests.get(f"{BASE_URL}/debug/status")
    print_response(response, "디버그 상태")
    
    # 3. 대기 (모니터링 실행 대기)
    print("\n3️⃣  모니터링 실행 대기 중... (60초)")
    print("   💡 이 시간 동안 터미널에서 상세 로그를 확인하세요!")
    for i in range(60, 0, -10):
        print(f"   ⏳ {i}초 남음...")
        time.sleep(10)
    
    # 4. 통계 확인
    print("\n4️⃣  디버그 통계 조회...")
    response = requests.get(f"{BASE_URL}/debug/status")
    print_response(response, "디버그 통계")
    
    # 5. 로그 통계 출력
    print("\n5️⃣  로그에 통계 출력...")
    response = requests.post(f"{BASE_URL}/debug/statistics")
    print_response(response, "통계 출력")
    
    # 6. 디버그 모드 비활성화
    print("\n6️⃣  디버그 모드 비활성화...")
    response = requests.post(f"{BASE_URL}/debug/disable")
    print_response(response, "디버그 모드 비활성화")
    
    print("\n✅ 테스트 완료!")
    print("\n💡 팁:")
    print("   - 로그 파일 또는 터미널에서 상세 로그를 확인하세요")
    print("   - 각 함수의 실행 시간과 호출 순서를 분석하세요")
    print("   - 병목 지점을 찾아 최적화하세요")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  테스트 중단됨")
    except requests.exceptions.ConnectionError:
        print("\n❌ 서버에 연결할 수 없습니다!")
        print("   서버가 실행 중인지 확인하세요: python main.py")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")


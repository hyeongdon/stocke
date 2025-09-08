class AccountManager {
    constructor(app) {
        this.app = app;
    }
    
    async loadAccountInfo() { /* 계좌 정보 로딩 */ }
    updateAccountBalance(data) { /* 잔고 업데이트 */ }
    updateHoldings(data) { /* 보유종목 업데이트 */ }
    updateTradingHistory(data) { /* 매매기록 업데이트 */ }
    showDataSourceWarning(dataType, source) { /* 데이터 소스 경고 */ }
}
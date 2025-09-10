class AccountManager {
    constructor(app) {
        this.app = app;
    }
    
    async loadAccountInfo() {
        try {
            // 계좌 잔고 정보 로드
            const balanceResponse = await fetch('/account/balance');
            const balanceData = await balanceResponse.json();
            this.updateAccountBalance(balanceData);
            
            // 보유종목 정보 로드
            const holdingsResponse = await fetch('/account/holdings');
            const holdingsData = await holdingsResponse.json();
            this.updateHoldings(holdingsData);
            
            // 거래내역 로드
            const historyResponse = await fetch('/account/history');
            const historyData = await historyResponse.json();
            this.updateTradingHistory(historyData);
            
        } catch (error) {
            console.error('계좌 정보 로드 실패:', error);
        }
    }
    
    updateAccountBalance(data) {
        const balanceContainer = document.getElementById('account-balance');
        if (!balanceContainer) return;
        
        // 계좌 타입 표시
        const accountType = data._account_type || '실계좌';
        const accountNumber = data.acnt_no || 'N/A';
        
        balanceContainer.innerHTML = `
            <div class="account-header">
                <h3>계좌 정보</h3>
                <div class="account-type ${accountType === '모의투자' ? 'mock-account' : 'real-account'}">
                    ${accountType} 계좌: ${accountNumber}
                </div>
            </div>
            <div class="balance-grid">
                <div class="balance-item">
                    <label>계좌명</label>
                    <span>${data.acnt_nm || 'N/A'}</span>
                </div>
                <div class="balance-item">
                    <label>지점명</label>
                    <span>${data.brch_nm || 'N/A'}</span>
                </div>
                <div class="balance-item">
                    <label>입금금액</label>
                    <span>${this.formatNumber(data.entr)}원</span>
                </div>
                <div class="balance-item">
                    <label>총 평가금액</label>
                    <span>${this.formatNumber(data.tot_est_amt)}원</span>
                </div>
                <div class="balance-item">
                    <label>총 매입금액</label>
                    <span>${this.formatNumber(data.tot_pur_amt)}원</span>
                </div>
                <div class="balance-item">
                    <label>총 손익금액</label>
                    <span class="${this.getProfitClass(data.lspft_amt)}">${this.formatNumber(data.lspft_amt)}원</span>
                </div>
                <div class="balance-item">
                    <label>총 손익률</label>
                    <span class="${this.getProfitClass(data.lspft_rt)}">${data.lspft_rt || '0.00'}%</span>
                </div>
            </div>
            ${this.showDataSourceWarning('계좌잔고', data._data_source)}
        `;
    }
    
    updateHoldings(data) {
        const holdingsContainer = document.getElementById('account-holdings');
        if (!holdingsContainer) return;
        
        const holdings = data.stk_acnt_evlt_prst || [];
        const accountType = data.acnt_type || '실계좌';
        
        holdingsContainer.innerHTML = `
            <div class="holdings-header">
                <h3>보유종목 (${accountType})</h3>
                <span class="holdings-count">${holdings.length}개 종목</span>
            </div>
            <div class="holdings-table">
                <table>
                    <thead>
                        <tr>
                            <th>종목명</th>
                            <th>보유수량</th>
                            <th>평균단가</th>
                            <th>현재가</th>
                            <th>평가금액</th>
                            <th>손익금액</th>
                            <th>손익률</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${holdings.map(stock => `
                            <tr>
                                <td>${stock.stk_nm}</td>
                                <td>${this.formatNumber(stock.rmnd_qty)}주</td>
                                <td>${this.formatNumber(stock.avg_prc)}원</td>
                                <td>${this.formatNumber(stock.cur_prc)}원</td>
                                <td>${this.formatNumber(stock.evlt_amt)}원</td>
                                <td class="${this.getProfitClass(stock.pl_amt)}">${this.formatNumber(stock.pl_amt)}원</td>
                                <td class="${this.getProfitClass(stock.pl_rt)}">${stock.pl_rt}%</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
            ${holdings.length === 0 ? '<div class="no-data">보유종목이 없습니다.</div>' : ''}
        `;
    }
    
    updateTradingHistory(data) {
        const historyContainer = document.getElementById('account-history');
        if (!historyContainer) return;
        
        const history = data.history || [];
        
        historyContainer.innerHTML = `
            <div class="history-header">
                <h3>거래내역</h3>
                <span class="history-count">${history.length}건</span>
            </div>
            <div class="history-table">
                <table>
                    <thead>
                        <tr>
                            <th>일자</th>
                            <th>시간</th>
                            <th>종목명</th>
                            <th>구분</th>
                            <th>수량</th>
                            <th>단가</th>
                            <th>금액</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${history.map(trade => `
                            <tr>
                                <td>${trade.date}</td>
                                <td>${trade.time}</td>
                                <td>${trade.stock_name}</td>
                                <td class="${trade.type === '매수' ? 'buy' : 'sell'}">${trade.type}</td>
                                <td>${this.formatNumber(trade.quantity)}주</td>
                                <td>${this.formatNumber(trade.price)}원</td>
                                <td>${this.formatNumber(trade.amount)}원</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
            ${history.length === 0 ? '<div class="no-data">거래내역이 없습니다.</div>' : ''}
        `;
    }
    
    showDataSourceWarning(dataType, source) {
        if (source === 'MOCK_DATA') {
            return `
                <div class="data-source-warning">
                    <i class="warning-icon">⚠️</i>
                    <span>${dataType} 데이터는 임시 데이터입니다. 실제 API 연결이 필요합니다.</span>
                </div>
            `;
        }
        return '';
    }
    
    formatNumber(num) {
        if (!num) return '0';
        return parseInt(num).toLocaleString();
    }
    
    getProfitClass(value) {
        const num = parseFloat(value);
        if (num > 0) return 'profit';
        if (num < 0) return 'loss';
        return 'neutral';
    }
}
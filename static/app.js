class StockMonitorApp {
    constructor() {
        this.selectedConditionId = null;
        this.refreshInterval = null;
        this.currentChart = null;
        this.currentStockCode = null;
        this.currentStockName = null;
        this.init();
        this.setupDebugLog();
    }
    
    setupDebugLog() {
        // 화면에 로그를 표시하는 함수
        this.logToScreen = (message, type = 'info') => {
            const debugLog = document.getElementById('debugLog');
            const debugLogContent = document.getElementById('debugLogContent');
            
            if (debugLog && debugLogContent) {
                debugLog.style.display = 'block';
                
                const timestamp = new Date().toLocaleTimeString();
                const logEntry = document.createElement('div');
                logEntry.className = `text-${type === 'error' ? 'danger' : type === 'warn' ? 'warning' : 'info'}`;
                logEntry.innerHTML = `<small>[${timestamp}] ${message}</small>`;
                
                debugLogContent.appendChild(logEntry);
                debugLogContent.scrollTop = debugLogContent.scrollHeight;
                
                // 최대 50개 로그만 유지
                while (debugLogContent.children.length > 50) {
                    debugLogContent.removeChild(debugLogContent.firstChild);
                }
            }
        };
        
        // console.log를 오버라이드하여 화면에도 표시
        const originalConsoleLog = console.log;
        const originalConsoleError = console.error;
        
        console.log = (...args) => {
            originalConsoleLog.apply(console, args);
            this.logToScreen(args.join(' '), 'info');
        };
        
        console.error = (...args) => {
            originalConsoleError.apply(console, args);
            this.logToScreen(args.join(' '), 'error');
        };
    }

    init() {
        this.bindEvents();
        this.loadConditions();
        this.checkMonitoringStatus();
        
        // 30초마다 자동 새로고침
        this.startAutoRefresh();
    }

    bindEvents() {
        document.getElementById('refreshConditions').addEventListener('click', () => {
            this.loadConditions();
        });

        document.getElementById('refreshStocks').addEventListener('click', () => {
            if (this.selectedConditionId) {
                this.loadStocks(this.selectedConditionId);
            }
        });

        document.getElementById('toggleMonitoring').addEventListener('click', () => {
            this.toggleMonitoring();
        });
    }

    async loadConditions() {
        try {
            console.log('조건식 목록 로딩 시작');
            this.showLoading('조건식 목록을 불러오는 중...');
            
            const response = await fetch('/conditions/');
            console.log('응답 상태:', response.status);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            console.log('받은 데이터:', data);
            
            // DOM이 준비될 때까지 잠시 대기
            await new Promise(resolve => setTimeout(resolve, 100));
            
            // API가 배열을 직접 반환하므로 data를 그대로 사용
            this.renderConditions(Array.isArray(data) ? data : []);
            console.log('조건식 목록 렌더링 완료');
        } catch (error) {
            console.error('조건식 목록 로딩 실패:', error);
            this.showError(`조건식 목록을 불러오는데 실패했습니다: ${error.message}`);
        } finally {
            console.log('조건식 목록 로딩 종료, 로딩 모달 숨김');
            this.hideLoading();
        }
    }

    renderConditions(conditions) {
        console.log('renderConditions 호출됨, 조건식 수:', conditions.length);
        const container = document.getElementById('conditionsList');
        
        if (!container) {
            console.error('conditionsList 컨테이너를 찾을 수 없습니다');
            return;
        }
        
        if (!Array.isArray(conditions) || conditions.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-search"></i>
                    <p>등록된 조건식이 없습니다.</p>
                </div>
            `;
            console.log('조건식이 없어서 빈 상태 표시');
            return;
        }

        try {
            const htmlContent = conditions.map(condition => {
                console.log('조건식 처리 중:', condition);
                return `
                    <div class="list-group-item condition-item" data-condition-id="${condition.id}">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="mb-1">${condition.condition_name || '이름 없음'}</h6>
                                <small class="text-muted">ID: ${condition.id}</small>
                            </div>
                            <span class="badge bg-primary rounded-pill">${condition.id}</span>
                        </div>
                    </div>
                `;
            }).join('');
            
            console.log('생성된 HTML:', htmlContent);
            container.innerHTML = htmlContent;
            console.log('HTML 삽입 완료, 컨테이너 내용:', container.innerHTML);

            // 조건식 클릭 이벤트 바인딩
            const items = container.querySelectorAll('.condition-item');
            console.log('찾은 조건식 아이템 수:', items.length);
            
            items.forEach((item, index) => {
                console.log(`아이템 ${index} 이벤트 바인딩:`, item);
                item.addEventListener('click', (e) => {
                    const conditionId = e.currentTarget.dataset.conditionId;
                    this.selectCondition(conditionId, e.currentTarget);
                });
            });
            
            console.log('조건식 목록 렌더링 및 이벤트 바인딩 완료');
        } catch (error) {
            console.error('조건식 렌더링 중 오류:', error);
            container.innerHTML = `
                <div class="alert alert-danger" role="alert">
                    <i class="fas fa-exclamation-triangle me-2"></i>
                    조건식 목록을 표시하는 중 오류가 발생했습니다: ${error.message}
                </div>
            `;
        }
    }

    selectCondition(conditionId, element) {
        // 이전 선택 해제
        document.querySelectorAll('.condition-item').forEach(item => {
            item.classList.remove('active');
        });

        // 새로운 선택
        element.classList.add('active');
        this.selectedConditionId = conditionId;
        
        // UI 업데이트
        document.getElementById('refreshStocks').disabled = false;
        
        // 주식 정보 로딩
        this.loadStocks(conditionId);
    }

    async loadStocks(conditionId) {
        try {
            this.showStocksLoading();
            const response = await fetch(`/conditions/${conditionId}/stocks`);
            const data = await response.json();
            
            this.renderStocks(data);
            this.updateLastRefreshTime();
        } catch (error) {
            console.error('주식 정보 로딩 실패:', error);
            this.showStocksError('주식 정보를 불러오는데 실패했습니다.');
        }
    }

    renderStocks(data) {
        const container = document.getElementById('stocksContent');
        
        console.log('주식 데이터 렌더링:', data);
    
        if (!data.stocks || data.stocks.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-chart-line"></i>
                    <p>조건에 해당하는 종목이 없습니다.</p>
                </div>
            `;
            return;
        }
    
        const tableHtml = `
            <div class="stocks-container">
                <table class="table table-hover stocks-table">
                    <thead>
                        <tr>
                            <th>종목코드</th>
                            <th>종목명</th>
                            <th>현재가</th>
                            <th>전일대비</th>
                            <th>등락률</th>
                            <th>거래량</th>
                            <th>차트</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.stocks.map(stock => {
                            // 백엔드에서 계산된 등락률 사용
                            const changeRate = parseFloat(stock.change_rate) || 0;
                            // prev_close가 이미 전일대비 변동가격이므로 그대로 사용
                            const priceDiff = parseFloat(stock.prev_close);
                            
                            return `
                                <tr>
                                    <td><code>${stock.code}</code></td>
                                    <td><strong>${stock.name}</strong></td>
                                    <td class="${this.getPriceClass(changeRate)}">
                                        ${this.formatPrice(stock.price)}
                                    </td>
                                    <td class="${this.getPriceClass(changeRate)}">
                                        ${this.formatPriceDiff(priceDiff)}
                                    </td>
                                    <td class="${this.getPriceClass(changeRate)}">
                                        ${this.formatChangeRate(changeRate)}
                                    </td>
                                    <td>${this.formatVolume(stock.volume)}</td>
                                    <td>
                                        <button class="btn btn-outline-primary btn-sm chart-btn"
                                                data-stock-code="${stock.code}" 
                                                data-stock-name="${stock.name}">
                                            <i class="fas fa-chart-line"></i>
                                        </button>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
    
        container.innerHTML = tableHtml;
        container.classList.add('fade-in');
        
        // 차트 버트 이벤트 바인딩
        this.bindChartEvents();
    }

    getPriceClass(changeRate) {
        const rate = parseFloat(changeRate);
        if (rate > 0) return 'price-up';
        if (rate < 0) return 'price-down';
        return 'price-neutral';
    }

    formatPrice(price) {
        return new Intl.NumberFormat('ko-KR').format(price);
    }

    formatChangeRate(rate) {
        const numRate = parseFloat(rate);
        const sign = numRate > 0 ? '+' : '';
        return `${sign}${numRate.toFixed(2)}%`;
    }

    formatVolume(volume) {
        return new Intl.NumberFormat('ko-KR').format(volume);
    }

    calculatePriceDiff(currentPrice, prevClose) {
        const current = parseFloat(currentPrice) || 0;
        const prev = parseFloat(prevClose) || 0;
        return current - prev;
    }

    calculateChangeRate(currentPrice, prevClose) {
        const current = parseFloat(currentPrice) || 0;
        const prev = parseFloat(prevClose) || 0;
        
        console.log('등락률 계산:', {
            currentPrice: currentPrice,
            prevClose: prevClose,
            current: current,
            prev: prev,
            diff: current - prev,
            rate: prev === 0 ? 0 : ((current - prev) / prev) * 100
        });
        
        if (prev === 0) return 0;
        return ((current - prev) / prev) * 100;
    }

    formatPriceDiff(diff) {
        const sign = diff > 0 ? '+' : diff < 0 ? '-' : '';
        return `${sign}${new Intl.NumberFormat('ko-KR').format(Math.abs(diff))}`;
    }

    async checkMonitoringStatus() {
        try {
            const response = await fetch('/monitoring/status');
            const data = await response.json();
            
            this.updateMonitoringStatus(data.is_running);
        } catch (error) {
            console.error('모니터링 상태 확인 실패:', error);
            this.updateMonitoringStatus(null);
        }
    }

    updateMonitoringStatus(isRunning) {
        const statusElement = document.getElementById('monitoringStatus');
        const toggleButton = document.getElementById('toggleMonitoring');
        
        if (isRunning === true) {
            statusElement.textContent = '실행 중';
            statusElement.className = 'badge bg-success me-3';
            toggleButton.textContent = '모니터링 중지';
            toggleButton.className = 'btn btn-sm btn-danger';
        } else if (isRunning === false) {
            statusElement.textContent = '중지됨';
            statusElement.className = 'badge bg-secondary me-3';
            toggleButton.textContent = '모니터링 시작';
            toggleButton.className = 'btn btn-sm btn-success';
        } else {
            statusElement.textContent = '상태 불명';
            statusElement.className = 'badge bg-warning me-3';
            toggleButton.textContent = '상태 확인';
            toggleButton.className = 'btn btn-sm btn-warning';
        }
        
        toggleButton.disabled = false;
    }

    async toggleMonitoring() {
        try {
            const statusResponse = await fetch('/monitoring/status');
            const statusData = await statusResponse.json();
            
            const endpoint = statusData.is_running ? '/monitoring/stop' : '/monitoring/start';
            const response = await fetch(endpoint, { method: 'POST' });
            
            if (response.ok) {
                // 상태 다시 확인
                setTimeout(() => this.checkMonitoringStatus(), 1000);
            } else {
                throw new Error('모니터링 상태 변경 실패');
            }
        } catch (error) {
            console.error('모니터링 토글 실패:', error);
            alert('모니터링 상태를 변경하는데 실패했습니다.');
        }
    }

    showLoading(message = '로딩 중...') {
        const modalElement = document.getElementById('loadingModal');
        const modalBody = modalElement.querySelector('.modal-body p');
        if (modalBody) {
            modalBody.textContent = message;
        }
        
        let modal = bootstrap.Modal.getInstance(modalElement);
        if (!modal) {
            modal = new bootstrap.Modal(modalElement);
        }
        modal.show();
    }

    hideLoading() {
        console.log('hideLoading 호출됨');
        const modalElement = document.getElementById('loadingModal');
        
        // Bootstrap 모달로 시도
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            console.log('Bootstrap 모달로 숨김 시도');
            modal.hide();
        }
        
        // 강제로 모달 숨김 (대체 방법)
        setTimeout(() => {
            console.log('강제 모달 정리 시작');
            
            // 모달 요소 숨김
            if (modalElement) {
                modalElement.style.display = 'none';
                modalElement.classList.remove('show');
                modalElement.setAttribute('aria-hidden', 'true');
                modalElement.removeAttribute('aria-modal');
            }
            
            // 백드롭 제거
            const backdrops = document.querySelectorAll('.modal-backdrop');
            backdrops.forEach(backdrop => backdrop.remove());
            
            // body 스타일 정리
            document.body.classList.remove('modal-open');
            document.body.style.removeProperty('overflow');
            document.body.style.removeProperty('padding-right');
            
            console.log('강제 모달 정리 완료');
        }, 100);
    }

    showStocksLoading() {
        const container = document.getElementById('stocksContent');
        container.innerHTML = `
            <div class="text-center p-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">로딩 중...</span>
                </div>
                <p class="mt-2 mb-0">주식 정보를 불러오는 중...</p>
            </div>
        `;
    }

    showStocksError(message) {
        const container = document.getElementById('stocksContent');
        container.innerHTML = `
            <div class="alert alert-danger" role="alert">
                <i class="fas fa-exclamation-triangle me-2"></i>
                ${message}
            </div>
        `;
    }

    showError(message) {
        const container = document.getElementById('conditionsList');
        container.innerHTML = `
            <div class="alert alert-danger" role="alert">
                <i class="fas fa-exclamation-triangle me-2"></i>
                ${message}
            </div>
        `;
    }

    updateLastRefreshTime() {
        const element = document.getElementById('lastUpdated');
        const now = new Date();
        element.textContent = `마지막 업데이트: ${now.toLocaleTimeString('ko-KR')}`;
    }

    startAutoRefresh() {
        this.refreshInterval = setInterval(() => {
            if (this.selectedConditionId) {
                this.loadStocks(this.selectedConditionId);
            }
            this.checkMonitoringStatus();
        }, 30000); // 30초마다 새로고침
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }
    
    bindChartEvents() {
        const chartButtons = document.querySelectorAll('.chart-btn');
        chartButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                e.preventDefault();
                const stockCode = button.getAttribute('data-stock-code');
                const stockName = button.getAttribute('data-stock-name');
                this.showStockChart(stockCode, stockName);
            });
        });
        
        // 차트 기간 변경 이벤트
        const periodButtons = document.querySelectorAll('input[name="chartPeriod"]');
        periodButtons.forEach(button => {
            button.addEventListener('change', () => {
                if (this.currentStockCode && button.checked) {
                    this.loadChartData(this.currentStockCode, button.value);
                }
            });
        });
    }
    
    async showStockChart(stockCode, stockName) {
        console.log(`차트 표시: ${stockCode} - ${stockName}`);
        
        this.currentStockCode = stockCode;
        this.currentStockName = stockName;
        
        // 모달 정보 업데이트
        document.getElementById('chartStockName').textContent = stockName;
        document.getElementById('chartStockCode').textContent = stockCode;
        
        // 모달 표시
        const chartModal = new bootstrap.Modal(document.getElementById('chartModal'));
        chartModal.show();
        
        // 기본 1일 차트 로드
        document.getElementById('period1D').checked = true;
        await this.loadChartData(stockCode, '1D');
    }
    
    async loadChartData(stockCode, period) {
        try {
            console.log(`캔들차트 로딩: ${stockCode}, 기간: ${period}`);
            
            // 로딩 표시
            this.showChartLoading();
            
            // 캔들차트 이미지 API 호출
            const response = await fetch(`/chart/image/${stockCode}?period=${period}`);
            const data = await response.json();
            
            if (response.ok) {
                this.renderCandlestickChart(data.image, stockCode);
            } else {
                this.showChartError('캔들차트를 불러올 수 없습니다.');
            }
        } catch (error) {
            console.error('캔들차트 로딩 실패:', error);
            this.showChartError('캔들차트 로딩 중 오류가 발생했습니다.');
        }
    }

    renderCandlestickChart(imageData, stockCode) {
        const container = document.getElementById('chartContainer');
        
        // 기존 차트 제거
        if (this.currentChart) {
            this.currentChart.destroy();
            this.currentChart = null;
        }
        
        // 캔들차트 이미지 표시
        container.innerHTML = `
            <div class="chart-image-container">
                <img src="${imageData}" 
                     alt="${stockCode} 캔들차트" 
                     style="max-width: 100%; max-height: 100%; width: auto; height: auto; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
            </div>
        `;
        
        console.log(`캔들차트 렌더링 완료: ${stockCode}`);
    }
    
    renderChart(chartData, period) {
        const ctx = document.getElementById('chartCanvas');
        
        // 기존 차트 제거
        if (this.currentChart) {
            this.currentChart.destroy();
        }
        
        // 캔버스 요소가 없으면 생성
        if (!ctx) {
            const container = document.getElementById('chartContainer');
            container.innerHTML = '<canvas id="chartCanvas"></canvas>';
        }
        
        const canvas = document.getElementById('chartCanvas');
        
        // 차트 데이터 준비
        const labels = chartData.map(item => {
            const date = new Date(item.timestamp);
            if (period === '1D') {
                return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
            } else {
                return date.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' });
            }
        });
        
        const prices = chartData.map(item => item.close);
        const volumes = chartData.map(item => item.volume);
        
        // 차트 생성
        this.currentChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: '주가',
                    data: prices,
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: 'rgba(75, 192, 192, 0.1)',
                    tension: 0.1,
                    fill: true,
                    yAxisID: 'y'
                }, {
                    label: '거래량',
                    data: volumes,
                    type: 'bar',
                    backgroundColor: 'rgba(255, 99, 132, 0.3)',
                    borderColor: 'rgba(255, 99, 132, 0.8)',
                    borderWidth: 1,
                    yAxisID: 'y1'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                scales: {
                    y: {
                        type: 'linear',
                        display: true,
                        position: 'left',
                        beginAtZero: false,
                        ticks: {
                            callback: function(value) {
                                return value.toLocaleString() + '원';
                            }
                        }
                    },
                    y1: {
                        type: 'linear',
                        display: true,
                        position: 'right',
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return value.toLocaleString();
                            }
                        },
                        grid: {
                            drawOnChartArea: false,
                        },
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                if (context.datasetIndex === 0) {
                                    return '주가: ' + context.parsed.y.toLocaleString() + '원';
                                } else {
                                    return '거래량: ' + context.parsed.y.toLocaleString();
                                }
                            }
                        }
                    }
                }
            }
        });
        
        console.log(`차트 렌더링 완료: ${chartData.length}개 데이터 포인트`);
    }
    
    showChartLoading() {
        const container = document.getElementById('chartContainer');
        container.innerHTML = `
            <div class="text-center p-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">차트 로딩 중...</span>
                </div>
                <p class="mt-2 mb-0">차트를 불러오는 중...</p>
            </div>
        `;
    }
    
    showChartError(message) {
        const container = document.getElementById('chartContainer');
        container.innerHTML = `
            <div class="alert alert-danger text-center" role="alert">
                <i class="fas fa-exclamation-triangle me-2"></i>
                ${message}
            </div>
        `;
    }
}

// 앱 초기화
document.addEventListener('DOMContentLoaded', () => {
    new StockMonitorApp();
});

// 페이지 언로드 시 자동 새로고침 중지
window.addEventListener('beforeunload', () => {
    if (window.stockApp) {
        window.stockApp.stopAutoRefresh();
    }
});

// 차트 이미지 로드 함수
async function loadChartImage(stockCode) {
    try {
        const response = await fetch(`/chart/image/${stockCode}`);
        const data = await response.json();
        
        // 차트 이미지 표시
        const chartContainer = document.getElementById('chart-container');
        chartContainer.innerHTML = `<img src="${data.image}" alt="${stockCode} 차트" style="max-width: 100%; height: auto;">`;
        
    } catch (error) {
        console.error('차트 로드 오류:', error);
    }
}

// 종목 클릭 시 차트 표시
function showChart(stockCode) {
    loadChartImage(stockCode);
}
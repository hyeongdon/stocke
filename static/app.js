class StockMonitorApp {
    constructor() {
        this.selectedConditionId = null;
        this.refreshInterval = null;
        this.currentChart = null;
        this.currentStockCode = null;
        this.currentStockName = null;
        this.selectedStockForNews = null;
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
        
        // 디버깅: 컨테이너 확인
        console.log('Container found:', container);
        console.log('Container current content:', container.innerHTML);
        
        if (!data || !data.stocks || data.stocks.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-5">
                    <i class="fas fa-chart-line fa-3x mb-3"></i>
                    <p>해당 조건식에 맞는 종목이 없습니다.</p>
                </div>
            `;
            return;
        }
    
        // 헤더 HTML (Bootstrap Grid 사용)
        const headerHtml = `
            <div class="stocks-header mb-2 py-2 bg-light border-bottom fw-bold" style="display: flex; background-color: #f8f9fa !important; border-bottom: 2px solid #dee2e6 !important; font-weight: bold !important; padding: 10px 0 !important; margin-bottom: 10px !important;">
                <div style="flex: 0 0 25%; padding: 0 15px;">종목명/코드</div>
                <div style="flex: 0 0 16.67%; padding: 0 15px; text-align: right;">현재가</div>
                <div style="flex: 0 0 16.67%; padding: 0 15px; text-align: right;">전일대비</div>
                <div style="flex: 0 0 16.67%; padding: 0 15px; text-align: right;">등락률</div>
                <div style="flex: 0 0 16.67%; padding: 0 15px; text-align: right;">거래량</div>
                <div style="flex: 0 0 8.33%; padding: 0 15px; text-align: right;">차트</div>
            </div>
        `;
        
        // 디버깅: 헤더 HTML 확인
        console.log('Header HTML:', headerHtml);
    
        const stocksHtml = data.stocks.map(stock => {
            // 데이터 변환: 백엔드 필드명에 맞게 수정
            const currentPrice = parseInt(stock.current_price) || 0;  // stock.price → stock.current_price
            const prevClose = parseInt(stock.prev_close) || 0;
            const backendChangeRate = parseFloat(stock.change_rate) || 0;
            const volume = parseInt(stock.volume) || 0;
            
            // 전일대비 계산 (현재가 - 전일종가)
            const priceDiff = currentPrice - prevClose;
            
            // 등락률은 항상 계산된 값 사용 (백엔드 값이 부정확함)
            const finalChangeRate = this.calculateChangeRate(currentPrice, prevClose);
            const priceClass = this.getPriceClass(finalChangeRate);
            
            // 디버깅용 로그
            console.log(`${stock.stock_name}:`);  // stock.name → stock.stock_name
            console.log(`  현재가: ${currentPrice}, 전일종가: ${prevClose}`);
            console.log(`  전일대비: ${priceDiff}, 백엔드등락률: ${backendChangeRate}%`);
            console.log(`  계산된등락률: ${finalChangeRate.toFixed(2)}%`);
            console.log(`  최종등락률: ${finalChangeRate.toFixed(2)}%, 클래스: ${priceClass}`);
            console.log('---');
            
            return `
                <div class="stock-item card mb-2" 
                     data-stock-code="${stock.stock_code}"
                     data-stock-name="${stock.stock_name}"
                     onclick="console.log('클릭됨: ${stock.stock_code}'); window.app.selectStockForNews('${stock.stock_code}', '${stock.stock_name}'); return false;">
                    <div class="card-body p-3">
                        <div class="row align-items-center">
                            <div class="col-md-3">
                                <h6 class="mb-1 fw-bold">${stock.stock_name}</h6>
                                <small class="text-muted">${stock.stock_code}</small>
                            </div>
                            <div class="col-md-2 text-end">
                                <div class="fw-bold ${priceClass}">
                                    ${this.formatPrice(currentPrice)}원
                                </div>
                            </div>
                            <div class="col-md-2 text-end">
                                <div class="${priceClass}">
                                    ${this.formatPriceDiff(priceDiff)}
                                </div>
                            </div>
                            <div class="col-md-2 text-end">
                                <div class="${priceClass}">
                                    ${this.formatChangeRate(finalChangeRate)}%
                                </div>
                            </div>
                            <div class="col-md-2 text-end">
                                <small class="text-muted">
                                    ${this.formatVolume(volume)}
                                </small>
                            </div>
                            <div class="col-md-1 text-end">
                                <button class="btn btn-outline-primary btn-sm" 
                                    onclick="event.stopPropagation(); window.app.showStockChart('${stock.stock_code}', '${stock.stock_name}')">
                                    <i class="fas fa-chart-line"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            }).join('');
            
            // 헤더와 종목 목록을 함께 설정
            const finalHtml = headerHtml + stocksHtml;
            console.log('Final HTML length:', finalHtml.length);
            console.log('Final HTML preview:', finalHtml.substring(0, 200));
            
            container.innerHTML = finalHtml;
            
            // 디버깅: 설정 후 확인
            console.log('After setting innerHTML:', container.innerHTML.substring(0, 200));
            console.log('Header element found:', container.querySelector('.stocks-header'));
        }

        // 가격 관련 함수들 (NaN 문제 해결)
        formatPrice(price) {
            if (!price || isNaN(parseFloat(price))) {
                return '0';
            }
            return parseInt(parseFloat(price)).toLocaleString();
        }

        formatChangeRate(rate) {
            if (!rate || isNaN(parseFloat(rate))) {
                return '0.00';
            }
            const numRate = parseFloat(rate);
            const sign = numRate > 0 ? '+' : '';
            return `${sign}${numRate.toFixed(2)}`;
        }

        formatPriceDiff(diff) {
            if (!diff || isNaN(parseFloat(diff))) {
                return '0';
            }
            const numDiff = parseFloat(diff);
            const sign = numDiff >= 0 ? '+' : '';
            return `${sign}${parseInt(numDiff).toLocaleString()}`;
        }

        formatVolume(volume) {
            if (!volume || isNaN(parseFloat(volume))) {
                return '0';
            }
            return parseInt(parseFloat(volume)).toLocaleString();
        }

        calculateChangeRate(currentPrice, prevClose) {
            if (!currentPrice || !prevClose || parseFloat(prevClose) === 0) {
                return 0;
            }
            
            const current = parseFloat(currentPrice);
            const prev = parseFloat(prevClose);
            
            if (isNaN(current) || isNaN(prev) || prev === 0) {
                return 0;
            }
            
            return ((current - prev) / prev * 100);
        }

        calculatePriceDiff(currentPrice, prevClose) {
            if (!currentPrice || !prevClose) {
                return 0;
            }
            
            const current = parseFloat(currentPrice);
            const prev = parseFloat(prevClose);
            
            if (isNaN(current) || isNaN(prev)) {
                return 0;
            }
            
            return current - prev;
        }

        getPriceClass(changeRate) {
            const rate = parseFloat(changeRate);
            if (isNaN(rate)) return 'price-neutral';
            if (rate > 0) return 'price-up';
            if (rate < 0) return 'price-down';
            return 'price-neutral';
        }

        showLoading(message = '로딩 중...') {
            const loadingElement = document.getElementById('loadingModal');
            if (loadingElement) {
                loadingElement.style.display = 'block';
                const messageElement = loadingElement.querySelector('.loading-message');
                if (messageElement) {
                    messageElement.textContent = message;
                }
            }
        }
        
        hideLoading() {
            const loadingElement = document.getElementById('loadingModal');
            if (loadingElement) {
                loadingElement.style.display = 'none';
            }
        }
        
        showStocksLoading() {
            const container = document.getElementById('stocksContent');
            if (container) {
                container.innerHTML = `
                    <div class="text-center py-5">
                        <div class="spinner-border text-primary" role="status">
                            <span class="visually-hidden">로딩 중...</span>
                        </div>
                        <p class="mt-3">종목 정보를 불러오는 중...</p>
                    </div>
                `;
            }
        }
        
        showError(message) {
            const container = document.getElementById('stocksContent');
            if (container) {
                container.innerHTML = `
                    <div class="alert alert-danger" role="alert">
                        <i class="fas fa-exclamation-triangle me-2"></i>
                        ${message}
                    </div>
                `;
            }
        }
        
        showStocksError(message) {
            this.showError(message);
        }
        
        async checkMonitoringStatus() {
            try {
                const response = await fetch('/monitoring/status');
                const data = await response.json();
                this.updateMonitoringUI(data.is_monitoring);
            } catch (error) {
                console.error('모니터링 상태 확인 실패:', error);
            }
        }
        
        updateMonitoringUI(isMonitoring) {
            const button = document.getElementById('toggleMonitoring');
            if (button) {
                button.textContent = isMonitoring ? '모니터링 중지' : '모니터링 시작';
                button.className = isMonitoring ? 'btn btn-danger' : 'btn btn-success';
            }
        }
        
        async toggleMonitoring() {
            try {
                const response = await fetch('/monitoring/toggle', { method: 'POST' });
                const data = await response.json();
                this.updateMonitoringUI(data.is_monitoring);
            } catch (error) {
                console.error('모니터링 토글 실패:', error);
            }
        }
        
        startAutoRefresh() {
            // 30초마다 자동 새로고침
            this.refreshInterval = setInterval(() => {
                if (this.selectedConditionId) {
                    this.loadStocks(this.selectedConditionId);
                }
            }, 30000);
        }
        
        stopAutoRefresh() {
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        }
        
        updateLastRefreshTime() {
            const timeElement = document.getElementById('lastRefreshTime');
            if (timeElement) {
                timeElement.textContent = new Date().toLocaleTimeString();
            }
        }
        
        selectStockForNews(stockCode, stockName) {
            this.selectedStockForNews = { code: stockCode, name: stockName };
            console.log('뉴스용 종목 선택:', stockCode, stockName);
            
            // 뉴스 섹션 표시
            const newsSection = document.getElementById('newsSection');
            const newsStockName = document.getElementById('newsStockName');
            const newsContent = document.getElementById('newsContent');
            
            if (newsSection && newsStockName && newsContent) {
                newsSection.style.display = 'block';
                newsStockName.textContent = stockName;
                
                // 로딩 상태 표시
                newsContent.innerHTML = `
                    <div class="text-center py-3">
                        <div class="spinner-border text-primary" role="status">
                            <span class="visually-hidden">로딩 중...</span>
                        </div>
                        <p class="mt-2">뉴스를 불러오는 중...</p>
                    </div>
                `;
                
                // 뉴스 로딩
                this.loadNews(stockCode, stockName);
            }
        }
        
        // 새로운 뉴스 로딩 함수 추가
        async loadNews(stockCode, stockName) {
            try {
                const response = await fetch(`/news/${stockCode}?stock_name=${encodeURIComponent(stockName)}`);
                const newsData = await response.json();
                
                const newsContent = document.getElementById('newsContent');
                
                if (newsData.error) {
                    newsContent.innerHTML = `
                        <div class="alert alert-warning" role="alert">
                            <i class="fas fa-exclamation-triangle me-2"></i>
                            ${newsData.error}
                        </div>
                    `;
                    return;
                }
                
                if (!newsData.items || newsData.items.length === 0) {
                    newsContent.innerHTML = `
                        <div class="text-center text-muted py-3">
                            <i class="fas fa-newspaper fa-2x mb-2"></i>
                            <p>관련 뉴스가 없습니다.</p>
                        </div>
                    `;
                    return;
                }
                
                // 뉴스 목록 렌더링
                const newsHtml = newsData.items.map(item => `
                    <div class="news-item border-bottom pb-3 mb-3">
                        <h6 class="news-title">
                            <a href="${item.link}" target="_blank" class="text-decoration-none">
                                ${item.title}
                            </a>
                        </h6>
                        <p class="news-description text-muted mb-2">${item.description}</p>
                        <small class="text-muted">
                            <i class="fas fa-calendar me-1"></i>
                            ${item.pubDate || '날짜 정보 없음'}
                        </small>
                    </div>
                `).join('');
                
                newsContent.innerHTML = newsHtml;
                
            } catch (error) {
                console.error('뉴스 로딩 오류:', error);
                const newsContent = document.getElementById('newsContent');
                newsContent.innerHTML = `
                    <div class="alert alert-danger" role="alert">
                        <i class="fas fa-exclamation-triangle me-2"></i>
                        뉴스를 불러오는데 실패했습니다: ${error.message}
                    </div>
                `;
            }
        }
        
        showStockChart(stockCode, stockName) {
            console.log('차트 표시:', stockCode, stockName);
            showChart(stockCode);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        window.app = new StockMonitorApp();
    });

    window.addEventListener('beforeunload', () => {
        if (window.app) {
            window.app.stopAutoRefresh();
        }
    });

    // 차트 이미지 로드 함수
    async function loadChartImage(stockCode) {
        try {
            const response = await fetch(`/chart/image/${stockCode}`);
            const data = await response.json();
            
            // 차트 이미지 표시 (올바른 ID 사용)
            const chartContainer = document.getElementById('chartContainer');
            if (chartContainer) {
                chartContainer.innerHTML = `<img src="${data.image}" alt="${stockCode} 차트" style="max-width: 100%; height: auto;">`;
            } else {
                console.error('chartContainer 요소를 찾을 수 없습니다.');
            }
            
        } catch (error) {
            console.error('차트 로드 오류:', error);
            const chartContainer = document.getElementById('chartContainer');
            if (chartContainer) {
                chartContainer.innerHTML = '<div class="text-center text-danger p-4"><i class="fas fa-exclamation-triangle"></i><p class="mt-2">차트를 불러올 수 없습니다.</p></div>';
            }
        }
    }

    // 종목 클릭 시 차트 표시
    function showChart(stockCode) {
        // 차트 모달 표시
        const chartModal = new bootstrap.Modal(document.getElementById('chartModal'));
        chartModal.show();
        
        // 차트 로드
        loadChartImage(stockCode);
    }

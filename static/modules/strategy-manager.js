/**
 * 전략매매 관리자 모듈
 * 관심종목 관리, 전략 설정, 전략 모니터링을 담당합니다.
 */

class StrategyManager {
    constructor() {
        this.watchlist = [];
        this.strategies = [];
        this.strategySignals = [];
        this.isMonitoring = false;
        this.monitoringStartTime = null;
        
        this.init();
    }

    async init() {
        console.log('🎯 [STRATEGY_MANAGER] 초기화 시작');
        
        // 이벤트 리스너 등록
        this.setupEventListeners();
        
        // 초기 데이터 로드
        await this.loadWatchlist();
        await this.loadStrategies();
        await this.loadStrategySignals();
        await this.loadStrategyStatus();
        
        console.log('🎯 [STRATEGY_MANAGER] 초기화 완료');
    }

    setupEventListeners() {
        // 관심종목 추가
        document.getElementById('addToWatchlist').addEventListener('click', () => {
            this.addToWatchlist();
        });

        // 관심종목 새로고침
        document.getElementById('refreshWatchlist').addEventListener('click', () => {
            this.loadWatchlist();
        });

        // 전략 모니터링 토글
        document.getElementById('strategyMonitoringToggle').addEventListener('change', (e) => {
            this.toggleStrategyMonitoring(e.target.checked);
        });

        // 전략 신호 새로고침
        document.getElementById('refreshStrategySignals').addEventListener('click', () => {
            this.loadStrategySignals();
        });

        // Enter 키로 관심종목 추가
        document.getElementById('addStockCode').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.addToWatchlist();
            }
        });
        document.getElementById('addStockName').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.addToWatchlist();
            }
        });
    }

    async addToWatchlist() {
        const stockCode = document.getElementById('addStockCode').value.trim();
        const stockName = document.getElementById('addStockName').value.trim();

        if (!stockCode || !stockName) {
            this.showAlert('종목코드와 종목명을 모두 입력해주세요.', 'warning');
            return;
        }

        try {
            const response = await fetch('/watchlist/add', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    stock_code: stockCode,
                    stock_name: stockName
                })
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                document.getElementById('addStockCode').value = '';
                document.getElementById('addStockName').value = '';
                await this.loadWatchlist();
            } else {
                this.showAlert(result.detail || '관심종목 추가에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('관심종목 추가 오류:', error);
            this.showAlert('관심종목 추가 중 오류가 발생했습니다.', 'danger');
        }
    }

    async loadWatchlist() {
        try {
            const response = await fetch('/watchlist/');
            const data = await response.json();

            if (response.ok) {
                this.watchlist = data.watchlist;
                this.renderWatchlist();
            } else {
                console.error('관심종목 로드 실패:', data);
            }
        } catch (error) {
            console.error('관심종목 로드 오류:', error);
        }
    }

    renderWatchlist() {
        const container = document.getElementById('watchlistContainer');
        
        if (this.watchlist.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-heart fa-2x mb-2"></i>
                    <p>관심종목이 없습니다.</p>
                </div>
            `;
            return;
        }

        const watchlistHtml = this.watchlist.map(stock => `
            <div class="d-flex justify-content-between align-items-center mb-2 p-2 border rounded">
                <div class="flex-grow-1">
                    <div class="fw-bold">${stock.stock_name}</div>
                    <small class="text-muted">${stock.stock_code}</small>
                </div>
                <div class="d-flex gap-1">
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" 
                               ${stock.is_active ? 'checked' : ''} 
                               onchange="strategyManager.toggleWatchlistStock('${stock.stock_code}', this.checked)">
                    </div>
                    <button class="btn btn-outline-danger btn-sm" 
                            onclick="strategyManager.removeFromWatchlist('${stock.stock_code}')">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');

        container.innerHTML = watchlistHtml;
    }

    async toggleWatchlistStock(stockCode, isActive) {
        try {
            const response = await fetch(`/watchlist/${stockCode}/toggle`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    stock_code: stockCode,
                    is_active: isActive
                })
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                await this.loadWatchlist();
            } else {
                this.showAlert(result.detail || '상태 변경에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('관심종목 상태 변경 오류:', error);
            this.showAlert('상태 변경 중 오류가 발생했습니다.', 'danger');
        }
    }

    async removeFromWatchlist(stockCode) {
        if (!confirm('정말로 이 종목을 관심종목에서 제거하시겠습니까?')) {
            return;
        }

        try {
            const response = await fetch(`/watchlist/${stockCode}`, {
                method: 'DELETE'
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                await this.loadWatchlist();
            } else {
                this.showAlert(result.detail || '관심종목 제거에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('관심종목 제거 오류:', error);
            this.showAlert('관심종목 제거 중 오류가 발생했습니다.', 'danger');
        }
    }

    async loadStrategies() {
        try {
            const response = await fetch('/strategies/');
            const data = await response.json();

            if (response.ok) {
                this.strategies = data.strategies;
                this.renderStrategies();
            } else {
                console.error('전략 로드 실패:', data);
            }
        } catch (error) {
            console.error('전략 로드 오류:', error);
        }
    }

    renderStrategies() {
        const container = document.getElementById('strategiesContainer');
        
        const strategiesHtml = this.strategies.map(strategy => `
            <div class="card mb-3">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span class="fw-bold">${strategy.strategy_name}</span>
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" 
                               ${strategy.is_enabled ? 'checked' : ''} 
                               onchange="strategyManager.toggleStrategy(${strategy.id}, this.checked)">
                    </div>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-6">
                            <small class="text-muted">타입</small>
                            <div class="fw-bold">${strategy.strategy_type}</div>
                        </div>
                        <div class="col-6">
                            <small class="text-muted">상태</small>
                            <div>
                                <span class="badge ${strategy.is_enabled ? 'bg-success' : 'bg-secondary'}">
                                    ${strategy.is_enabled ? '활성' : '비활성'}
                                </span>
                            </div>
                        </div>
                    </div>
                    <div class="mt-2">
                        <button class="btn btn-outline-primary btn-sm" 
                                onclick="strategyManager.configureStrategy('${strategy.strategy_type}', ${strategy.id})">
                            <i class="fas fa-cog"></i> 설정
                        </button>
                    </div>
                </div>
            </div>
        `).join('');

        container.innerHTML = strategiesHtml;
    }

    async toggleStrategy(strategyId, isEnabled) {
        try {
            const response = await fetch(`/strategies/${strategyId}/toggle`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    strategy_id: strategyId,
                    is_enabled: isEnabled
                })
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                await this.loadStrategies();
            } else {
                this.showAlert(result.detail || '전략 상태 변경에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('전략 상태 변경 오류:', error);
            this.showAlert('전략 상태 변경 중 오류가 발생했습니다.', 'danger');
        }
    }

    configureStrategy(strategyType, strategyId) {
        const strategy = this.strategies.find(s => s.id === strategyId);
        if (!strategy) return;

        const parameters = strategy.parameters || {};
        
        let configHtml = '';
        
        switch (strategyType) {
            case 'MOMENTUM':
                configHtml = `
                    <div class="mb-3">
                        <label class="form-label">모멘텀 기간</label>
                        <input type="number" class="form-control" id="momentumPeriod" 
                               value="${parameters.momentum_period || 24}" min="5" max="50">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">추세 확인 기간</label>
                        <input type="number" class="form-control" id="trendConfirmationDays" 
                               value="${parameters.trend_confirmation_days || 3}" min="1" max="10">
                    </div>
                `;
                break;
            case 'DISPARITY':
                configHtml = `
                    <div class="mb-3">
                        <label class="form-label">이동평균 기간</label>
                        <input type="number" class="form-control" id="maPeriod" 
                               value="${parameters.ma_period || 20}" min="5" max="50">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">매수 임계값 (%)</label>
                        <input type="number" class="form-control" id="buyThreshold" 
                               value="${parameters.buy_threshold || 95}" min="80" max="100" step="0.1">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">매도 임계값 (%)</label>
                        <input type="number" class="form-control" id="sellThreshold" 
                               value="${parameters.sell_threshold || 105}" min="100" max="120" step="0.1">
                    </div>
                `;
                break;
            case 'BOLLINGER':
                configHtml = `
                    <div class="mb-3">
                        <label class="form-label">이동평균 기간</label>
                        <input type="number" class="form-control" id="maPeriod" 
                               value="${parameters.ma_period || 20}" min="5" max="50">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">표준편차 배수</label>
                        <input type="number" class="form-control" id="stdMultiplier" 
                               value="${parameters.std_multiplier || 2}" min="1" max="3" step="0.1">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">확인 기간</label>
                        <input type="number" class="form-control" id="confirmationDays" 
                               value="${parameters.confirmation_days || 3}" min="1" max="10">
                    </div>
                `;
                break;
            case 'RSI':
                configHtml = `
                    <div class="mb-3">
                        <label class="form-label">RSI 기간</label>
                        <input type="number" class="form-control" id="rsiPeriod" 
                               value="${parameters.rsi_period || 7}" min="5" max="30">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">과매도 임계값</label>
                        <input type="number" class="form-control" id="oversoldThreshold" 
                               value="${parameters.oversold_threshold || 30}" min="10" max="40" step="0.1">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">과매수 임계값</label>
                        <input type="number" class="form-control" id="overboughtThreshold" 
                               value="${parameters.overbought_threshold || 70}" min="60" max="90" step="0.1">
                    </div>
                `;
                break;
            case 'ICHIMOKU':
                configHtml = `
                    <div class="mb-3">
                        <label class="form-label">전환선 기간 (5분봉 개수)</label>
                        <input type="number" class="form-control" id="conversionPeriod" 
                               value="${parameters.conversion_period || 9}" min="5" max="20">
                        <div class="form-text">기본값: 9개 봉 (45분)</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">기준선 기간 (5분봉 개수)</label>
                        <input type="number" class="form-control" id="basePeriod" 
                               value="${parameters.base_period || 26}" min="15" max="50">
                        <div class="form-text">기본값: 26개 봉 (2시간 10분)</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">선행스팬B 기간 (5분봉 개수)</label>
                        <input type="number" class="form-control" id="spanBPeriod" 
                               value="${parameters.span_b_period || 52}" min="30" max="100">
                        <div class="form-text">기본값: 52개 봉 (4시간 20분)</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">후행스팬 이동 기간</label>
                        <input type="number" class="form-control" id="displacement" 
                               value="${parameters.displacement || 26}" min="15" max="50">
                        <div class="form-text">기본값: 26개 봉</div>
                    </div>
                `;
                break;
            case 'CHAIKIN':
                configHtml = `
                    <div class="mb-3">
                        <label class="form-label">단기 이동평균 기간</label>
                        <input type="number" class="form-control" id="shortPeriod" 
                               value="${parameters.short_period || 3}" min="2" max="10">
                        <div class="form-text">AD 라인의 단기 이동평균 기간</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">장기 이동평균 기간</label>
                        <input type="number" class="form-control" id="longPeriod" 
                               value="${parameters.long_period || 10}" min="5" max="20">
                        <div class="form-text">AD 라인의 장기 이동평균 기간</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">매수 신호 임계값</label>
                        <input type="number" class="form-control" id="buyThreshold" 
                               value="${parameters.buy_threshold || 0}" min="-50" max="50" step="0.1">
                        <div class="form-text">차이킨 오실레이터가 이 값 이상일 때 매수 신호</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">매도 신호 임계값</label>
                        <input type="number" class="form-control" id="sellThreshold" 
                               value="${parameters.sell_threshold || 0}" min="-50" max="50" step="0.1">
                        <div class="form-text">차이킨 오실레이터가 이 값 이하일 때 매도 신호</div>
                    </div>
                `;
                break;
        }

        const modalHtml = `
            <div class="modal fade" id="strategyConfigModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">${strategy.strategy_name} 설정</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            ${configHtml}
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">취소</button>
                            <button type="button" class="btn btn-primary" onclick="strategyManager.saveStrategyConfig('${strategyType}', ${strategyId})">저장</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 기존 모달 제거
        const existingModal = document.getElementById('strategyConfigModal');
        if (existingModal) {
            existingModal.remove();
        }

        // 새 모달 추가
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        // 모달 표시
        const modal = new bootstrap.Modal(document.getElementById('strategyConfigModal'));
        modal.show();
    }

    async saveStrategyConfig(strategyType, strategyId) {
        let parameters = {};
        
        switch (strategyType) {
            case 'MOMENTUM':
                parameters = {
                    momentum_period: parseInt(document.getElementById('momentumPeriod').value),
                    trend_confirmation_days: parseInt(document.getElementById('trendConfirmationDays').value)
                };
                break;
            case 'DISPARITY':
                parameters = {
                    ma_period: parseInt(document.getElementById('maPeriod').value),
                    buy_threshold: parseFloat(document.getElementById('buyThreshold').value),
                    sell_threshold: parseFloat(document.getElementById('sellThreshold').value)
                };
                break;
            case 'BOLLINGER':
                parameters = {
                    ma_period: parseInt(document.getElementById('maPeriod').value),
                    std_multiplier: parseFloat(document.getElementById('stdMultiplier').value),
                    confirmation_days: parseInt(document.getElementById('confirmationDays').value)
                };
                break;
            case 'RSI':
                parameters = {
                    rsi_period: parseInt(document.getElementById('rsiPeriod').value),
                    oversold_threshold: parseFloat(document.getElementById('oversoldThreshold').value),
                    overbought_threshold: parseFloat(document.getElementById('overboughtThreshold').value)
                };
                break;
            case 'ICHIMOKU':
                parameters = {
                    conversion_period: parseInt(document.getElementById('conversionPeriod').value),
                    base_period: parseInt(document.getElementById('basePeriod').value),
                    span_b_period: parseInt(document.getElementById('spanBPeriod').value),
                    displacement: parseInt(document.getElementById('displacement').value)
                };
                break;
            case 'CHAIKIN':
                parameters = {
                    short_period: parseInt(document.getElementById('shortPeriod').value),
                    long_period: parseInt(document.getElementById('longPeriod').value),
                    buy_threshold: parseFloat(document.getElementById('buyThreshold').value),
                    sell_threshold: parseFloat(document.getElementById('sellThreshold').value)
                };
                break;
        }

        try {
            const response = await fetch(`/strategies/${strategyType}/configure`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    strategy_type: strategyType,
                    parameters: parameters
                })
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                bootstrap.Modal.getInstance(document.getElementById('strategyConfigModal')).hide();
                await this.loadStrategies();
            } else {
                this.showAlert(result.detail || '전략 설정 저장에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('전략 설정 저장 오류:', error);
            this.showAlert('전략 설정 저장 중 오류가 발생했습니다.', 'danger');
        }
    }

    async toggleStrategyMonitoring(isEnabled) {
        try {
            const endpoint = isEnabled ? '/strategy/start' : '/strategy/stop';
            const response = await fetch(endpoint, {
                method: 'POST'
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                this.isMonitoring = isEnabled;
                if (isEnabled) {
                    this.monitoringStartTime = new Date();
                }
                await this.loadStrategyStatus();
            } else {
                this.showAlert(result.detail || '전략 모니터링 제어에 실패했습니다.', 'danger');
                // 토글 상태 되돌리기
                document.getElementById('strategyMonitoringToggle').checked = !isEnabled;
            }
        } catch (error) {
            console.error('전략 모니터링 제어 오류:', error);
            this.showAlert('전략 모니터링 제어 중 오류가 발생했습니다.', 'danger');
            // 토글 상태 되돌리기
            document.getElementById('strategyMonitoringToggle').checked = !isEnabled;
        }
    }

    async loadStrategyStatus() {
        try {
            const response = await fetch('/strategy/status');
            const data = await response.json();

            if (response.ok) {
                this.isMonitoring = data.is_running;
                document.getElementById('strategyMonitoringToggle').checked = this.isMonitoring;
                
                const statusText = this.isMonitoring ? '실행 중' : '중지';
                const statusClass = this.isMonitoring ? 'bg-success' : 'bg-secondary';
                
                document.getElementById('strategyStatusText').textContent = statusText;
                document.getElementById('strategyStatusText').className = `badge ${statusClass}`;
                
                if (this.isMonitoring && this.monitoringStartTime) {
                    const runTime = Math.floor((new Date() - this.monitoringStartTime) / 1000 / 60);
                    document.getElementById('strategyRunTime').textContent = `${runTime}분`;
                } else {
                    document.getElementById('strategyRunTime').textContent = '-';
                }
            }
        } catch (error) {
            console.error('전략 상태 로드 오류:', error);
        }
    }

    async loadStrategySignals() {
        try {
            // 모든 전략의 신호를 가져오기 위해 각 전략별로 요청
            const signalsPromises = this.strategies.map(strategy => 
                fetch(`/signals/by-strategy/${strategy.id}?limit=10`)
                    .then(response => response.json())
                    .then(data => ({ strategy, signals: data.signals || [] }))
                    .catch(error => ({ strategy, signals: [] }))
            );

            const results = await Promise.all(signalsPromises);
            this.strategySignals = results.flatMap(result => 
                result.signals.map(signal => ({ ...signal, strategy_name: result.strategy.strategy_name }))
            );

            this.renderStrategySignals();
        } catch (error) {
            console.error('전략 신호 로드 오류:', error);
        }
    }

    renderStrategySignals() {
        const container = document.getElementById('strategySignalsContainer');
        
        if (this.strategySignals.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-signal fa-2x mb-2"></i>
                    <p>전략 신호가 없습니다.</p>
                </div>
            `;
            return;
        }

        // 최신 신호부터 정렬
        const sortedSignals = this.strategySignals.sort((a, b) => 
            new Date(b.detected_at) - new Date(a.detected_at)
        );

        const signalsHtml = sortedSignals.slice(0, 20).map(signal => {
            const signalClass = signal.signal_type === 'BUY' ? 'text-success' : 'text-danger';
            const signalIcon = signal.signal_type === 'BUY' ? 'fa-arrow-up' : 'fa-arrow-down';
            const detectedTime = new Date(signal.detected_at).toLocaleString('ko-KR');
            
            return `
                <div class="d-flex justify-content-between align-items-center mb-2 p-2 border rounded">
                    <div class="flex-grow-1">
                        <div class="fw-bold">${signal.stock_name}</div>
                        <small class="text-muted">${signal.stock_code} • ${signal.strategy_name}</small>
                    </div>
                    <div class="text-end">
                        <div class="${signalClass}">
                            <i class="fas ${signalIcon}"></i> ${signal.signal_type}
                        </div>
                        <small class="text-muted">${detectedTime}</small>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = signalsHtml;
    }

    showAlert(message, type = 'info') {
        // 기존 알림 제거
        const existingAlert = document.querySelector('.alert');
        if (existingAlert) {
            existingAlert.remove();
        }

        // 새 알림 생성
        const alertHtml = `
            <div class="alert alert-${type} alert-dismissible fade show position-fixed" 
                 style="top: 20px; right: 20px; z-index: 1060; min-width: 300px;">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', alertHtml);

        // 3초 후 자동 제거
        setTimeout(() => {
            const alert = document.querySelector('.alert');
            if (alert) {
                alert.remove();
            }
        }, 3000);
    }
}

// 전역 인스턴스 생성
window.strategyManager = new StrategyManager();

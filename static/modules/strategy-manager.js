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
        this.syncStatusInterval = null;  // 동기화 상태 업데이트 인터벌
        
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
        await this.loadWatchlistSyncStatus();
        await this.loadWatchlistSyncConfig();
        
        // 동기화 상태 주기적 업데이트 시작 (30초마다)
        this.startSyncStatusUpdates();
        
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

        // 관심종목 동기화 토글
        document.getElementById('watchlistSyncToggle').addEventListener('change', (e) => {
            this.toggleWatchlistSync(e.target.checked);
        });

        // 동기화 설정 저장
        document.getElementById('saveSyncConfig').addEventListener('click', () => {
            this.saveWatchlistSyncConfig();
        });

        // 수동 동기화
        document.getElementById('manualSyncBtn').addEventListener('click', () => {
            this.manualWatchlistSync();
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
                // 동기화 상태도 함께 업데이트
                await this.loadWatchlistSyncStatus();
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

        // 수기등록과 조건식 종목 분리
        const manualStocks = this.watchlist.filter(stock => stock.source_type === 'MANUAL');
        const conditionStocks = this.watchlist.filter(stock => stock.source_type === 'CONDITION');

        let watchlistHtml = '';

        // 수기등록 종목 섹션
        if (manualStocks.length > 0) {
            watchlistHtml += `
                <div class="mb-3">
                    <h6 class="text-primary mb-2">
                        <i class="fas fa-hand-paper me-1"></i>수기등록 종목 (${manualStocks.length}개)
                    </h6>
                    ${manualStocks.map(stock => `
                        <div class="d-flex justify-content-between align-items-center mb-2 p-2 border rounded bg-light">
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
                    `).join('')}
                </div>
            `;
        }

        // 조건식 종목 섹션
        if (conditionStocks.length > 0) {
            // 조건식별로 그룹화
            const groupedByCondition = conditionStocks.reduce((groups, stock) => {
                const conditionName = stock.condition_name || '알 수 없는 조건식';
                if (!groups[conditionName]) {
                    groups[conditionName] = [];
                }
                groups[conditionName].push(stock);
                return groups;
            }, {});

            watchlistHtml += `
                <div class="mb-3">
                    <h6 class="text-success mb-2">
                        <i class="fas fa-robot me-1"></i>조건식 종목 (${conditionStocks.length}개)
                    </h6>
                    ${Object.entries(groupedByCondition).map(([conditionName, stocks]) => `
                        <div class="mb-2">
                            <small class="text-muted fw-bold">${conditionName}</small>
                            ${stocks.map(stock => `
                                <div class="d-flex justify-content-between align-items-center mb-1 p-2 border rounded bg-light">
                                    <div class="flex-grow-1">
                                        <div class="fw-bold">${stock.stock_name}</div>
                                        <small class="text-muted">${stock.stock_code}</small>
                                        ${stock.condition_status === 'REMOVED' ? 
                                            '<small class="text-warning"><i class="fas fa-exclamation-triangle me-1"></i>조건식에서 제거됨</small>' : 
                                            '<small class="text-success"><i class="fas fa-check-circle me-1"></i>활성</small>'
                                        }
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
                            `).join('')}
                        </div>
                    `).join('')}
                </div>
            `;
        }

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
            case 'MA1592':
                configHtml = `
                    <div class="alert alert-info small">15/92 홀드 — <b>3분봉 EMA15가 EMA92 상향돌파</b> 후 분할 매수. 익절=전고 50%.</div>
                    <div class="mb-3">
                        <label class="form-label">MA 소스 / 타입</label>
                        <select class="form-select" id="ma1592MaSource">
                            <option value="bar" ${(parameters.ma_source || 'bar') === 'bar' ? 'selected' : ''}>bar (5분봉)</option>
                            <option value="daily_overlay" ${parameters.ma_source === 'daily_overlay' ? 'selected' : ''}>daily_overlay (일봉 오버레이·예비)</option>
                        </select>
                        <input type="hidden" id="ma1592MaType" value="${parameters.ma_type || 'ema'}">
                        <div class="form-text">기본: 3분 EMA15 / EMA92</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">최소 거래대금 (원)</label>
                        <input type="number" class="form-control" id="ma1592MinValue"
                               value="${parameters.min_trading_value || 5000000000}" step="100000000">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">홀드 봉수 (5분)</label>
                        <input type="number" class="form-control" id="ma1592HoldBars"
                               value="${parameters.hold_bars || 6}" min="1" max="30">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">매수 전 15선 이탈 %</label>
                        <input type="number" class="form-control" id="ma1592BreakPre"
                               value="${parameters.break_before_entry_pct || 0.4}" step="0.1">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">전고 lookback (일)</label>
                        <input type="number" class="form-control" id="ma1592PrevHighDays"
                               value="${parameters.prev_high_lookback_days || 20}" min="5" max="60">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">TP1 비율</label>
                        <input type="number" class="form-control" id="ma1592Tp1Frac"
                               value="${parameters.tp1_frac || 0.5}" min="0.1" max="0.9" step="0.1">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">하드/큰이탈/급락 % · 손절%</label>
                        <div class="input-group">
                            <input type="number" class="form-control" id="ma1592Hard" value="${parameters.hard_break_pct || 1.0}" step="0.1" title="hard">
                            <input type="number" class="form-control" id="ma1592Large" value="${parameters.large_break_pct ?? 0.7}" step="0.1" title="impulse 후 EMA92 이탈%">
                            <input type="number" class="form-control" id="ma1592Crash" value="${parameters.crash_pct ?? 1.8}" step="0.1" title="고점 대비 급락%">
                            <input type="number" class="form-control" id="ma1592Stop" value="${parameters.stop_pct || 4.0}" step="0.1" title="stop">
                        </div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">장부 TTL / 최대보유 (일)</label>
                        <div class="input-group">
                            <input type="number" class="form-control" id="ma1592Expire" value="${parameters.setup_expire_days || 8}">
                            <input type="number" class="form-control" id="ma1592MaxHold" value="${parameters.max_hold_days || 10}">
                        </div>
                    </div>
                    <div class="form-check mb-3">
                        <input class="form-check-input" type="checkbox" id="ma1592Flatten"
                               ${(parameters.flatten_eod ? 'checked' : '')}>
                        <label class="form-check-label" for="ma1592Flatten">장종료 전량청산 (flatten_eod)</label>
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
            case 'MA1592':
                parameters = {
                    ma_source: document.getElementById('ma1592MaSource').value,
                    ma_type: document.getElementById('ma1592MaType')?.value || 'ema',
                    min_trading_value: parseInt(document.getElementById('ma1592MinValue').value),
                    hold_bars: parseInt(document.getElementById('ma1592HoldBars').value),
                    break_before_entry_pct: parseFloat(document.getElementById('ma1592BreakPre').value),
                    prev_high_lookback_days: parseInt(document.getElementById('ma1592PrevHighDays').value),
                    tp1_frac: parseFloat(document.getElementById('ma1592Tp1Frac').value),
                    hard_break_pct: parseFloat(document.getElementById('ma1592Hard').value),
                    large_break_pct: parseFloat(document.getElementById('ma1592Large').value),
                    crash_pct: parseFloat(document.getElementById('ma1592Crash').value),
                    stop_pct: parseFloat(document.getElementById('ma1592Stop').value),
                    setup_expire_days: parseInt(document.getElementById('ma1592Expire').value),
                    max_hold_days: parseInt(document.getElementById('ma1592MaxHold').value),
                    flatten_eod: document.getElementById('ma1592Flatten').checked,
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
                
                if (this.isMonitoring && data.running_time_minutes !== undefined) {
                    document.getElementById('strategyRunTime').textContent = `${data.running_time_minutes}분`;
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
            // 1) 기존 방식: DB의 TradingStrategy별 신호 조회
            const signalsPromises = this.strategies.map(strategy =>
                fetch(`/signals/by-strategy/${strategy.id}?limit=20`)
                    .then(response => response.json())
                    .then(data => ({ strategy, signals: data.signals || [] }))
                    .catch(() => ({ strategy, signals: [] }))
            );

            const results = await Promise.all(signalsPromises);
            let byStrategy = results.flatMap(result =>
                result.signals.map(signal => ({ ...signal, strategy_key: result.strategy.strategy_type, strategy_name: result.strategy.strategy_name }))
            );

            // 2) 추가: 매수대기(PENDING)/관측(WATCHING) 신호도 가져와서 'additional_data.strategy' 키로 그룹에 포함
            try {
                const pendingRes = await fetch('/signals/pending?limit=200');
                const pendingJson = await pendingRes.json();
                const pendingItems = pendingJson.items || pendingJson.signals || [];
                // Normalize pending items into same shape
                const normalized = (pendingItems || []).map(it => {
                    const additional = it.additional_data || {};
                    let strat = null;
                    try {
                        if (typeof additional === 'string') {
                            const parsed = JSON.parse(additional || '{}');
                            strat = parsed.strategy;
                        } else {
                            strat = additional.strategy;
                        }
                    } catch (_) {
                        strat = additional.strategy || null;
                    }
                    return {
                        id: it.id || it.signal_id || null,
                        stock_code: it.stock_code,
                        stock_name: it.stock_name,
                        signal_type: it.status || it.signal_type || 'BUY',
                        detected_at: it.detected_at,
                        status: it.status,
                        additional_data: additional,
                        strategy_name: strat || 'auto_trade'
                    };
                });
                byStrategy = byStrategy.concat(normalized);
            } catch (e) {
                console.warn('전략 신호에 PENDING 병합 실패:', e);
            }

            this.strategySignals = byStrategy;
            this.renderStrategySignals();
        } catch (error) {
            console.error('전략 신호 로드 오류:', error);
        }
    }

    renderStrategySignals() {
        const container = document.getElementById('strategySignalsContainer');
        if (!container) return;

        if (this.strategySignals.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-signal fa-2x mb-2"></i>
                    <p>전략 신호가 없습니다.</p>
                </div>
            `;
            return;
        }

        // 그룹화: strategy_name을 키로 사용
        const groups = {};
        this.strategySignals.forEach(s => {
            const key = (s.strategy_name || s.additional_data?.strategy || 'unknown') || 'unknown';
            if (!groups[key]) groups[key] = [];
            groups[key].push(s);
        });

        // 탭 헤더 생성
        const strategyKeys = Object.keys(groups).sort((a,b) => a.localeCompare(b));
        let tabsHtml = '<ul class="nav nav-tabs mb-2" id="strategySignalTabs" role="tablist">';
        strategyKeys.forEach((k, idx) => {
            const count = groups[k].length;
            tabsHtml += `<li class="nav-item" role="presentation">
                <button class="nav-link ${idx===0?'active':''}" id="tab-${esc(k)}" data-bs-toggle="tab" data-key="${esc(k)}" type="button" role="tab">${esc(k)} <span class="badge bg-secondary ms-1">${count}</span></button>
            </li>`;
        });
        tabsHtml += '</ul>';

        // 탭 콘텐츠: 첫 탭만 보이도록
        let contentHtml = '<div id="strategySignalsTabsContent">';
        strategyKeys.forEach((k, idx) => {
            const items = groups[k].sort((a,b) => new Date(b.detected_at) - new Date(a.detected_at)).slice(0, 50);
            const listHtml = items.map(signal => {
                const signalClass = (signal.signal_type || signal.status || 'BUY') === 'BUY' ? 'text-success' : 'text-danger';
                const signalIcon = (signal.signal_type || signal.status || 'BUY') === 'BUY' ? 'fa-arrow-up' : 'fa-arrow-down';
                const detectedTime = signal.detected_at ? new Date(signal.detected_at).toLocaleString('ko-KR') : '';
                return `<div class="d-flex justify-content-between align-items-center mb-2 p-2 border rounded">
                    <div class="flex-grow-1">
                        <div class="fw-bold">${esc(signal.stock_name)}</div>
                        <small class="text-muted">${esc(signal.stock_code)} • ${esc(k)}</small>
                    </div>
                    <div class="text-end">
                        <div class="${signalClass}">
                            <i class="fas ${signalIcon}"></i> ${esc(signal.signal_type || signal.status || '')}
                        </div>
                        <small class="text-muted">${detectedTime}</small>
                    </div>
                </div>`;
            }).join('');
            contentHtml += `<div class="tab-pane ${idx===0?'active show':''}" id="pane-${esc(k)}" data-key="${esc(k)}">${listHtml}</div>`;
        });
        contentHtml += '</div>';

        container.innerHTML = tabsHtml + contentHtml;

        // 탭 버튼 클릭으로 컨텐츠 스와이치
        document.querySelectorAll('#strategySignalTabs button[data-key]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const key = btn.getAttribute('data-key');
                document.querySelectorAll('#strategySignalsTabsContent .tab-pane').forEach(p => {
                    p.classList.remove('active','show');
                });
                const pane = document.querySelector(`#pane-${CSS.escape(key)}`);
                if (pane) pane.classList.add('active','show');
            });
        });
    }

    async toggleWatchlistSync(isEnabled) {
        try {
            const endpoint = isEnabled ? '/watchlist/sync/start' : '/watchlist/sync/stop';
            const response = await fetch(endpoint, {
                method: 'POST'
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert(result.message, 'success');
                await this.loadWatchlistSyncStatus();
            } else {
                this.showAlert(result.detail || '관심종목 동기화 제어에 실패했습니다.', 'danger');
                // 토글 상태 되돌리기
                document.getElementById('watchlistSyncToggle').checked = !isEnabled;
            }
        } catch (error) {
            console.error('관심종목 동기화 제어 오류:', error);
            this.showAlert('관심종목 동기화 제어 중 오류가 발생했습니다.', 'danger');
            // 토글 상태 되돌리기
            document.getElementById('watchlistSyncToggle').checked = !isEnabled;
        }
    }

    async loadWatchlistSyncStatus() {
        try {
            console.log('🔄 [STRATEGY_MANAGER] 동기화 상태 로드 중...');
            const response = await fetch('/watchlist/sync/status');
            const data = await response.json();

            if (response.ok) {
                console.log('📊 [STRATEGY_MANAGER] 동기화 상태 데이터:', data);
                
                // 동기화 상태 업데이트
                document.getElementById('watchlistSyncToggle').checked = data.is_running;
                
                // 통계 업데이트
                document.getElementById('manualStocksCount').textContent = data.manual_stocks || 0;
                document.getElementById('conditionStocksCount').textContent = data.condition_stocks || 0;
                document.getElementById('activeConditionsCount').textContent = data.active_conditions || 0;
                
                // 마지막 동기화 시간 업데이트
                if (data.last_sync_time) {
                    const syncTime = new Date(data.last_sync_time).toLocaleString('ko-KR');
                    document.getElementById('lastSyncTime').textContent = syncTime;
                } else {
                    document.getElementById('lastSyncTime').textContent = '-';
                }
                
                // 실행시간 업데이트
                if (data.is_running && data.running_time_minutes !== undefined) {
                    console.log(`⏰ [STRATEGY_MANAGER] 실행시간 업데이트: ${data.running_time_minutes}분`);
                    document.getElementById('runningTime').textContent = `${data.running_time_minutes}분`;
                } else {
                    console.log('⏰ [STRATEGY_MANAGER] 실행시간: 0분 (동기화 중지됨)');
                    document.getElementById('runningTime').textContent = '0분';
                }
            } else {
                console.error('❌ [STRATEGY_MANAGER] 동기화 상태 로드 실패:', data);
            }
        } catch (error) {
            console.error('❌ [STRATEGY_MANAGER] 관심종목 동기화 상태 로드 오류:', error);
        }
    }

    async loadWatchlistSyncConfig() {
        try {
            const response = await fetch('/watchlist/sync/config');
            const config = await response.json();

            if (response.ok) {
                // 설정 값들을 UI에 반영
                document.getElementById('syncOnlyTargetConditions').checked = config.sync_only_target_conditions;
                document.getElementById('targetConditionNames').value = config.target_condition_names.join(', ');
            }
        } catch (error) {
            console.error('관심종목 동기화 설정 로드 오류:', error);
        }
    }

    async saveWatchlistSyncConfig() {
        try {
            const syncOnlyTarget = document.getElementById('syncOnlyTargetConditions').checked;
            const targetNames = document.getElementById('targetConditionNames').value
                .split(',')
                .map(name => name.trim())
                .filter(name => name.length > 0);

            const config = {
                sync_only_target_conditions: syncOnlyTarget,
                target_condition_names: targetNames
            };

            const response = await fetch('/watchlist/sync/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(config)
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert('동기화 설정이 저장되었습니다.', 'success');
            } else {
                this.showAlert(result.detail || '동기화 설정 저장에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('동기화 설정 저장 오류:', error);
            this.showAlert('동기화 설정 저장 중 오류가 발생했습니다.', 'danger');
        }
    }

    async manualWatchlistSync() {
        try {
            const response = await fetch('/watchlist/sync/manual', {
                method: 'POST'
            });

            const result = await response.json();

            if (response.ok) {
                this.showAlert('수동 동기화가 완료되었습니다.', 'success');
                await this.loadWatchlist();
                await this.loadWatchlistSyncStatus();
            } else {
                this.showAlert(result.detail || '수동 동기화에 실패했습니다.', 'danger');
            }
        } catch (error) {
            console.error('수동 동기화 오류:', error);
            this.showAlert('수동 동기화 중 오류가 발생했습니다.', 'danger');
        }
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

    startSyncStatusUpdates() {
        // 기존 인터벌 정리
        if (this.syncStatusInterval) {
            clearInterval(this.syncStatusInterval);
        }
        
        // 10초마다 동기화 상태 및 전략 상태 업데이트 (더 빠른 업데이트)
        this.syncStatusInterval = setInterval(() => {
            this.loadWatchlistSyncStatus();
            this.loadStrategyStatus();
        }, 10000); // 10초
        
        console.log('🔄 [STRATEGY_MANAGER] 동기화 상태 주기적 업데이트 시작 (10초마다)');
    }

    stopSyncStatusUpdates() {
        if (this.syncStatusInterval) {
            clearInterval(this.syncStatusInterval);
            this.syncStatusInterval = null;
            console.log('🛑 [STRATEGY_MANAGER] 동기화 상태 주기적 업데이트 중지');
        }
    }
}

// 전역 인스턴스 생성
window.strategyManager = new StrategyManager();

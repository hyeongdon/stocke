class StockMonitorApp {
    constructor() {
        this.init();
    }

    init() {
        this.bindEvents();
        this.bindTabEvents();
        this.bindMobileEvents();
        this.loadTradingSettings();
        console.log('🔍 [PENDING] init: scheduling initial load');
        const pendingListEl = document.getElementById('pendingList');
        const refreshBtn = document.getElementById('refreshPending');
        console.log('🔍 [PENDING] DOM probe:', {
            pendingListFound: !!pendingListEl,
            refreshBtnFound: !!refreshBtn
        });
        setTimeout(() => {
            console.log('🔍 [PENDING] calling loadPendingSignals()');
            this.loadPendingSignals();
        }, 100);
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
            alert('조건식 주기 검색은 중단되었습니다.\n대시보드(/dashboard)에서 자동매매를 시작하세요.');
            window.location.href = '/dashboard';
        });

        const refreshPendingBtn = document.getElementById('refreshPending');
        if (refreshPendingBtn) {
            refreshPendingBtn.addEventListener('click', () => this.loadPendingSignals());
        }

        const cleanupPendingBtn = document.getElementById('cleanupPending');
        if (cleanupPendingBtn) {
            cleanupPendingBtn.addEventListener('click', () => this.cleanupPendingSignals());
        }

        const mobileCleanupPendingBtn = document.getElementById('mobileCleanupPending');
        if (mobileCleanupPendingBtn) {
            mobileCleanupPendingBtn.addEventListener('click', () => this.cleanupPendingSignals());
        }

        const refreshAccountBtn = document.getElementById('refreshAccount');
        if (refreshAccountBtn) {
            refreshAccountBtn.addEventListener('click', () => this.loadAccountInfo());
        }

        // 선택 해제 버튼 이벤트
        const clearSelectionBtn = document.getElementById('clearSelection');
        if (clearSelectionBtn) {
            clearSelectionBtn.addEventListener('click', () => this.clearStockSelection());
        }

        const saveTradingSettingsBtn = document.getElementById('saveTradingSettings');
        if (saveTradingSettingsBtn) {
            saveTradingSettingsBtn.addEventListener('click', () => this.saveTradingSettings());
        }
    }

    // 모바일 이벤트 바인딩
    bindMobileEvents() {
        // 모바일 하단 네비게이션 이벤트
        const mobileNavItems = document.querySelectorAll('.mobile-nav-item');
        mobileNavItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const tabName = item.dataset.tab;
                this.switchMobileTab(tabName);
            });
        });

        // 모바일 전용 버튼 이벤트
        const mobileRefreshPendingBtn = document.getElementById('mobileRefreshPending');
        if (mobileRefreshPendingBtn) {
            mobileRefreshPendingBtn.addEventListener('click', () => this.loadMobilePendingSignals());
        }

        const mobileRefreshSignalsBtn = document.getElementById('mobileRefreshSignals');
        if (mobileRefreshSignalsBtn) {
            mobileRefreshSignalsBtn.addEventListener('click', () => this.loadMobileSignals());
        }

        const mobileRefreshAccountBtn = document.getElementById('mobileRefreshAccount');
        if (mobileRefreshAccountBtn) {
            mobileRefreshAccountBtn.addEventListener('click', () => this.loadMobileAccountSummary());
        }

        const mobileMonitoringToggle = document.getElementById('mobileMonitoringToggle');
        if (mobileMonitoringToggle) {
            mobileMonitoringToggle.addEventListener('change', () => {
                this.toggleMobileMonitoring(mobileMonitoringToggle.checked);
            });
        }

        // 터치 제스처 지원
        this.initTouchGestures();
    }

    // 모바일 탭 전환
    switchMobileTab(tabName) {
        console.log('모바일 탭 전환:', tabName);
        
        // 모든 모바일 네비게이션 아이템 비활성화
        document.querySelectorAll('.mobile-nav-item').forEach(item => {
            item.classList.remove('active');
        });

        // 선택된 아이템 활성화
        const selectedItem = document.querySelector(`[data-tab="${tabName}"]`);
        if (selectedItem) {
            selectedItem.classList.add('active');
        }

        // 모든 탭 콘텐츠 숨기기
        document.querySelectorAll('.tab-pane').forEach(pane => {
            pane.classList.remove('show', 'active');
        });

        // 선택된 탭 콘텐츠 표시
        let targetPane;
        switch(tabName) {
            case 'stocks':
                targetPane = document.getElementById('stocks-pane');
                break;
            case 'strategy':
                targetPane = document.getElementById('strategy-pane');
                break;
            case 'account':
                targetPane = document.getElementById('account-pane');
                break;
            case 'monitoring':
                targetPane = document.getElementById('monitoring-pane');
                break;
        }

        if (targetPane) {
            targetPane.classList.add('show', 'active');
            this.currentTab = tabName;
            
            // 모니터링 탭으로 전환 시 데이터 로드
            if (tabName === 'monitoring') {
                this.loadMobileMonitoringData();
            }
        }
    }

    // 모바일 모니터링 데이터 로드
    async loadMobileMonitoringData() {
        try {
            await Promise.all([
                this.loadMobilePendingSignals(),
                this.loadMobileSignals(),
                this.loadMobileAccountSummary(),
                this.checkMobileMonitoringStatus()
            ]);
        } catch (error) {
            console.error('모바일 모니터링 데이터 로드 실패:', error);
        }
    }

    // 모바일 매수대기 목록 로드
    async loadMobilePendingSignals() {
        try {
            const container = document.getElementById('mobilePendingList');
            if (!container) return;

            container.innerHTML = `
                <div class="text-center py-3">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">로딩 중...</span>
                    </div>
                </div>
            `;

            const response = await fetch('/signals/pending');
            const data = await response.json();
            const items = (data && Array.isArray(data.items)) ? data.items : [];

            if (items.length === 0) {
                container.innerHTML = `
                    <div class="text-center text-muted py-4">
                        <i class="fas fa-inbox fa-2x mb-2"></i>
                        <p>매수대기 종목이 없습니다.</p>
                    </div>
                `;
                return;
            }

            const html = items.map(item => {
                const time = item.detected_at ? new Date(item.detected_at).toLocaleTimeString() : '';
                const currentPrice = item.current_price ? item.current_price.toLocaleString() + '원' : '조회중...';
                const targetAmount = item.target_amount ? item.target_amount.toLocaleString() + '원' : '계산중...';
                
                return `
                    <div class="border-bottom py-2">
                        <div class="d-flex justify-content-between align-items-start">
                            <div class="flex-grow-1">
                                <div class="fw-bold">${item.stock_name}</div>
                                <small class="text-muted">${item.stock_code}</small>
                                <div class="mt-1">
                                    <small class="text-primary fw-bold">${currentPrice}</small>
                                    <small class="text-success ms-2">${targetAmount}</small>
                                </div>
                                <small class="text-muted">${time}</small>
                            </div>
                            <span class="badge bg-secondary">${item.status}</span>
                        </div>
                    </div>
                `;
            }).join('');

            container.innerHTML = html;
            
            // 매수대기 개수 업데이트
            const pendingCountEl = document.getElementById('mobilePendingCount');
            if (pendingCountEl) {
                pendingCountEl.textContent = items.length;
            }
        } catch (error) {
            console.error('모바일 매수대기 목록 로드 실패:', error);
            const container = document.getElementById('mobilePendingList');
            if (container) {
                container.innerHTML = `<div class="text-danger">로드 실패</div>`;
            }
        }
    }

    // 모바일 신호 목록 로드
    async loadMobileSignals() {
        try {
            const container = document.getElementById('mobileSignalsList');
            if (!container) return;

            container.innerHTML = `
                <div class="text-center py-3">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">로딩 중...</span>
                    </div>
                </div>
            `;

            // 최근 신호 API 호출 - pending API 사용
            const response = await fetch('/signals/pending?limit=20');
            const data = await response.json();
            const signals = (data && Array.isArray(data.items)) ? data.items : [];

            if (signals.length === 0) {
                container.innerHTML = `
                    <div class="text-center text-muted py-4">
                        <i class="fas fa-signal fa-2x mb-2"></i>
                        <p>최근 신호가 없습니다.</p>
                    </div>
                `;
                return;
            }

            const html = signals.map(signal => {
                const time = signal.created_at ? new Date(signal.created_at).toLocaleTimeString() : '';
                const signalType = signal.signal_type === 'BUY' ? '매수' : '매도';
                const signalClass = signal.signal_type === 'BUY' ? 'text-danger' : 'text-primary';
                
                return `
                    <div class="border-bottom py-2">
                        <div class="d-flex justify-content-between align-items-start">
                            <div class="flex-grow-1">
                                <div class="fw-bold">${signal.stock_name}</div>
                                <small class="text-muted">${signal.stock_code}</small>
                                <div class="mt-1">
                                    <span class="badge ${signalClass}">${signalType}</span>
                                    <small class="text-muted ms-2">${time}</small>
                                </div>
                            </div>
                            <div class="text-end">
                                <div class="fw-bold">${signal.price ? signal.price.toLocaleString() + '원' : '-'}</div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');

            container.innerHTML = html;
        } catch (error) {
            console.error('모바일 신호 목록 로드 실패:', error);
            const container = document.getElementById('mobileSignalsList');
            if (container) {
                container.innerHTML = `<div class="text-danger">로드 실패</div>`;
            }
        }
    }

    // 모바일 계좌 요약 로드
    async loadMobileAccountSummary() {
        try {
            const response = await fetch('/account/balance');
            const data = await response.json();

            // 총 자산 업데이트
            const totalAssetsEl = document.getElementById('mobileTotalAssets');
            if (totalAssetsEl) {
                const assets = parseInt(data.aset_evlt_amt || 0);
                totalAssetsEl.textContent = this.formatPrice(assets) + '원';
            }

            // 평가 손익 업데이트
            const totalProfitLossEl = document.getElementById('mobileTotalProfitLoss');
            if (totalProfitLossEl) {
                const profit = parseInt(data.lspft || 0);
                totalProfitLossEl.textContent = this.formatPriceDiff(profit) + '원';
                totalProfitLossEl.className = this.getPriceClass(profit);
            }

            // 수익률 업데이트
            const profitRateEl = document.getElementById('mobileProfitRate');
            if (profitRateEl) {
                const rate = parseFloat(data.lspft_rt || 0);
                profitRateEl.textContent = this.formatChangeRate(rate) + '%';
                profitRateEl.className = this.getPriceClass(rate);
            }

            // 보유 종목 개수 업데이트
            const holdingsResponse = await fetch('/account/holdings');
            const holdingsData = await holdingsResponse.json();
            const holdingsCountEl = document.getElementById('mobileHoldingsCount');
            if (holdingsCountEl && holdingsData.stk_acnt_evlt_prst) {
                holdingsCountEl.textContent = holdingsData.stk_acnt_evlt_prst.length;
            }

        } catch (error) {
            console.error('모바일 계좌 요약 로드 실패:', error);
        }
    }

    // 모바일 모니터링 상태 확인
    async checkMobileMonitoringStatus() {
        try {
            const response = await fetch('/monitoring/status');
            const data = await response.json();
            const isRunning = data.monitoring?.is_running || data.is_running || data.is_monitoring;
            
            this.updateMobileMonitoringUI(isRunning);
        } catch (error) {
            console.error('모바일 모니터링 상태 확인 실패:', error);
            this.updateMobileMonitoringUI(false);
        }
    }

    // 모바일 모니터링 UI 업데이트
    updateMobileMonitoringUI(isMonitoring) {
        const statusEl = document.getElementById('mobileMonitoringStatus');
        const toggleEl = document.getElementById('mobileMonitoringToggle');
        
        if (statusEl) {
            statusEl.textContent = isMonitoring ? '실행중' : '중지';
            statusEl.className = `badge ${isMonitoring ? 'bg-success' : 'bg-secondary'}`;
        }
        
        if (toggleEl) {
            toggleEl.checked = isMonitoring;
        }
    }

    // 모바일 모니터링 토글
    async toggleMobileMonitoring(enabled) {
        try {
            const endpoint = enabled ? '/monitoring/start' : '/monitoring/stop';
            const response = await fetch(endpoint, { method: 'POST' });
            
            if (response.ok) {
                this.updateMobileMonitoringUI(enabled);
            } else {
                throw new Error(`HTTP ${response.status}`);
            }
        } catch (error) {
            console.error('모바일 모니터링 토글 실패:', error);
            alert('모니터링 상태 변경에 실패했습니다.');
        }
    }

    // 터치 제스처 초기화
    initTouchGestures() {
        let startX = 0;
        let startY = 0;
        let isScrolling = false;

        document.addEventListener('touchstart', (e) => {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            isScrolling = false;
        }, { passive: true });

        document.addEventListener('touchmove', (e) => {
            if (!startX || !startY) return;
            
            const diffX = startX - e.touches[0].clientX;
            const diffY = startY - e.touches[0].clientY;
            
            if (Math.abs(diffX) > Math.abs(diffY)) {
                // 수평 스와이프
                if (Math.abs(diffX) > 50) {
                    isScrolling = true;
                }
            } else {
                // 수직 스와이프
                if (Math.abs(diffY) > 50) {
                    isScrolling = true;
                }
            }
        }, { passive: true });

        document.addEventListener('touchend', (e) => {
            if (!startX || !startY || isScrolling) return;
            
            const diffX = startX - e.changedTouches[0].clientX;
            const diffY = startY - e.changedTouches[0].clientY;
            
            if (Math.abs(diffX) > Math.abs(diffY)) {
                // 수평 스와이프 감지
                if (Math.abs(diffX) > 50) {
                    const tabs = ['stocks', 'strategy', 'account', 'monitoring'];
                    const currentIndex = tabs.indexOf(this.currentTab);
                    
                    if (diffX > 0 && currentIndex < tabs.length - 1) {
                        // 오른쪽으로 스와이프 (다음 탭)
                        this.switchMobileTab(tabs[currentIndex + 1]);
                    } else if (diffX < 0 && currentIndex > 0) {
                        // 왼쪽으로 스와이프 (이전 탭)
                        this.switchMobileTab(tabs[currentIndex - 1]);
                    }
                }
            }
            
            startX = 0;
            startY = 0;
            isScrolling = false;
        }, { passive: true });
    }

    // 탭 이벤트 바인딩 메서드 수정
    bindTabEvents() {
        const stockTab = document.getElementById('stocks-tab'); // 수정
        const accountTab = document.getElementById('account-tab'); // 수정
        console.log('탭 요소 찾기:', { stockTab, accountTab }); // 디버깅용
        
        if (stockTab) {
            stockTab.addEventListener('click', (e) => {
                e.preventDefault();
                console.log('종목 탭 클릭됨'); // 디버깅용
                this.switchTab('stock');
            });
        } else {
            console.error('stocks-tab 요소를 찾을 수 없습니다');
        }
        
        if (accountTab) {
            accountTab.addEventListener('click', (e) => {
                e.preventDefault();
                console.log('계좌 탭 클릭됨'); // 디버깅용
                this.switchTab('account');
            });
        } else {
            console.error('account-tab 요소를 찾을 수 없습니다');
        }
    }

    // 탭 전환 메서드도 수정
    switchTab(tabName) {
        console.log('탭 전환:', tabName); // 디버깅용
        
        // 탭 버튼 활성화 상태 변경
        const stockTab = document.getElementById('stocks-tab'); // 수정
        const accountTab = document.getElementById('account-tab'); // 수정
        const stockContent = document.getElementById('stocks-pane'); // 수정
        const accountContent = document.getElementById('account-pane'); // 수정
        
        console.log('요소 찾기:', { stockTab, accountTab, stockContent, accountContent }); // 디버깅용
        
        // 모든 탭 비활성화
        if (stockTab) stockTab.classList.remove('active');
        if (accountTab) accountTab.classList.remove('active');
        
        // 모든 콘텐츠 숨기기
        if (stockContent) {
            stockContent.classList.remove('show', 'active');
        }
        if (accountContent) {
            accountContent.classList.remove('show', 'active');
        }
        
        // 선택된 탭 활성화
        if (tabName === 'stock') {
            if (stockTab) stockTab.classList.add('active');
            if (stockContent) {
                stockContent.classList.add('show', 'active');
            }
            this.currentTab = 'stock';
            
            // 종목 탭으로 전환 시 자동 새로고침 재시작
            this.startAutoRefresh();
        } else if (tabName === 'account') {
            if (accountTab) accountTab.classList.add('active');
            if (accountContent) {
                accountContent.classList.add('show', 'active');
            }
            this.currentTab = 'account';
            
            // 계좌 탭으로 전환 시 계좌 정보 로드
            this.loadAccountInfo();
            
            // 종목 탭이 아닐 때는 자동 새로고침 중지
            this.stopAutoRefresh();
        }
    }

    async cleanupPendingSignals() {
        console.log('🧹 [CLEANUP] cleanupPendingSignals invoked');
        
        // 확인 대화상자
        if (!confirm('매수대기 목록을 정리하시겠습니까?\n\n정리된 신호는 복구할 수 없습니다.')) {
            return;
        }
        
        try {
            const response = await fetch('/cleanup/manual', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();
            console.log('🧹 [CLEANUP] cleanup result:', result);
            
            // 성공 메시지 표시
            this.showAlert(result.message || `매수대기 신호 ${result.cleaned_count}개가 정리되었습니다.`, 'success');
            
            // 목록 새로고침
            await this.loadPendingSignals();
            
        } catch (error) {
            console.error('🧹 [CLEANUP] cleanup error:', error);
            this.showAlert('매수대기 정리 중 오류가 발생했습니다.', 'danger');
        }
    }

    // 계좌 정보 로드 메서드 추가
    async loadAccountInfo() {
        console.log('🔍 [DEBUG] loadAccountInfo 시작');
        try {
            console.log('🔍 [DEBUG] API 호출 시작 - /account/balance');
            const balanceResponse = await fetch('/account/balance');
            console.log('🔍 [DEBUG] Balance Response Status:', balanceResponse.status);
            const balanceData = await balanceResponse.json();
            console.log('🔍 [DEBUG] Balance Data:', balanceData);
            
            // 데이터 소스 확인 및 사용자에게 알림
            if (balanceData._data_source === 'MOCK_DATA') {
                console.warn('⚠️ [DATA SOURCE] 임시 데이터를 사용 중입니다!');
                console.warn('⚠️ [DATA SOURCE] API 연결 상태:', balanceData._api_connected);
                console.warn('⚠️ [DATA SOURCE] 토큰 유효성:', balanceData._token_valid);
                
                // 사용자에게 시각적으로 알림
                this.showDataSourceWarning('계좌 정보', 'MOCK_DATA');
            } else if (balanceData._data_source === 'REAL_API') {
                console.log('✅ [DATA SOURCE] 실제 키움 API 데이터를 사용 중입니다.');
                this.hideDataSourceWarning();
            }
            
            if (balanceResponse.ok) {
                console.log('🔍 [DEBUG] updateAccountBalance 호출 전');
                this.updateAccountBalance(balanceData);
                console.log('🔍 [DEBUG] updateAccountBalance 호출 후');
                this.updateAccountInfo(balanceData);
            } else {
                console.error('🔍 [DEBUG] Balance API 에러:', balanceData);
            }
            
            console.log('🔍 [DEBUG] API 호출 시작 - /account/holdings');
            const holdingsResponse = await fetch('/account/holdings');
            console.log('🔍 [DEBUG] Holdings Response Status:', holdingsResponse.status);
            const holdingsData = await holdingsResponse.json();
            console.log('🔍 [DEBUG] Holdings Data:', holdingsData);
            
            // 보유종목 데이터 소스 확인
            if (holdingsData._data_source === 'MOCK_DATA') {
                console.warn('⚠️ [DATA SOURCE] 보유종목 임시 데이터를 사용 중입니다!');
                this.showDataSourceWarning('보유종목', 'MOCK_DATA');
            }
            
            if (holdingsResponse.ok) {
                console.log('🔍 [DEBUG] updateHoldings 호출 전');
                this.updateHoldings(holdingsData);
                console.log('🔍 [DEBUG] updateHoldings 호출 후');
            } else {
                console.error('🔍 [DEBUG] Holdings API 에러:', holdingsData);
            }
            
        } catch (error) {
            console.error('🔍 [DEBUG] 계좌 정보 로딩 실패:', error);
            this.showAccountError('계좌 정보를 불러오는데 실패했습니다.');
        }
    }

    async loadPendingSignals() {
        console.log('🔍 [PENDING] loadPendingSignals invoked');
        try {
            const container = document.getElementById('pendingList');
            console.log('🔍 [PENDING] lookup #pendingList =>', !!container);
            if (!container) {
                console.warn('🔍 [PENDING] #pendingList not found');
                return;
            }
            container.innerHTML = `
                <div class="text-center py-3">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">로딩 중...</span>
                    </div>
                </div>
            `;

            console.log('🔍 [PENDING] fetching /signals/pending');
            const res = await fetch('/signals/pending');
            console.log('🔍 [PENDING] fetch status:', res.status, res.statusText);
            let data = null;
            try {
                data = await res.json();
            } catch (parseErr) {
                const txt = await res.text().catch(() => '');
                console.error('🔍 [PENDING] response parse error:', parseErr, 'raw=', txt);
            }
            console.log('🔍 [PENDING] response JSON:', data);
            const items = (data && Array.isArray(data.items)) ? data.items : [];
            console.log('🔍 [PENDING] items.length =', items.length, 'total =', data && data.total);

            if (items.length === 0) {
                container.innerHTML = `
                    <div class="text-center text-muted py-4">
                        <i class="fas fa-inbox fa-2x mb-2"></i>
                        <p>매수대기 종목이 없습니다.</p>
                    </div>
                `;
                return;
            }

            const html = items.map(it => {
                const time = it.detected_at ? new Date(it.detected_at).toLocaleTimeString() : '';
                const currentPrice = it.current_price ? it.current_price.toLocaleString() + '원' : '조회중...';
                const targetAmount = it.target_amount ? it.target_amount.toLocaleString() + '원' : '계산중...';
                const targetQuantity = it.target_quantity || 0;
                
                // 대량거래 전략인지 확인
                const isVolumeSpike = it.condition_id === 999;
                const strategyText = isVolumeSpike ? '대량거래 전략' : `조건식 ${it.condition_id}`;
                
                return `
                    <div class="d-flex justify-content-between align-items-center border-bottom py-2">
                        <div class="flex-grow-1">
                            <div class="fw-bold">${it.stock_name} <small class="text-muted">(${it.stock_code})</small></div>
                            <div class="row mt-1">
                                <div class="col-6">
                                    <small class="text-muted">현재가: <span class="text-primary fw-bold">${currentPrice}</span></small>
                                </div>
                                <div class="col-6">
                                    <small class="text-muted">매수금액: <span class="text-success fw-bold">${targetAmount}</span></small>
                                </div>
                            </div>
                            <div class="row">
                                <div class="col-6">
                                    <small class="text-muted">${isVolumeSpike ? '매수수량: 1주' : `매수수량: ${targetQuantity}주`}</small>
                                </div>
                                <div class="col-6">
                                    <small class="text-muted">${strategyText} • ${time}</small>
                                </div>
                            </div>
                        </div>
                        <div class="text-end">
                            <span class="badge bg-secondary">${it.status}</span>
                        </div>
                    </div>
                `;
            }).join('');

            container.innerHTML = html;
        } catch (e) {
            console.error('🔍 [PENDING] load error:', e && e.stack ? e.stack : e);
            const container = document.getElementById('pendingList');
            if (container) {
                container.innerHTML = `<div class="text-danger">매수대기 목록을 불러오지 못했습니다.</div>`;
            }
        }
    }
    
    // 데이터 소스 경고 표시 메서드 추가
    showDataSourceWarning(dataType, source) {
        const warningId = `data-source-warning-${dataType.replace(/\s+/g, '-')}`;
        
        // 기존 경고가 있으면 제거
        const existingWarning = document.getElementById(warningId);
        if (existingWarning) {
            existingWarning.remove();
        }
        
        // 새 경고 메시지 생성
        const warningDiv = document.createElement('div');
        warningDiv.id = warningId;
        warningDiv.className = 'alert alert-warning alert-dismissible fade show mt-2';
        warningDiv.innerHTML = `
            <i class="fas fa-exclamation-triangle me-2"></i>
            <strong>임시 데이터 사용 중:</strong> ${dataType} 정보가 실제 키움 API가 아닌 임시 데이터로 표시되고 있습니다.
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        // 계좌 탭 상단에 경고 추가
        const accountPane = document.getElementById('account-pane');
        if (accountPane) {
            accountPane.insertBefore(warningDiv, accountPane.firstChild);
        }
    }
    
    // 데이터 소스 경고 숨김 메서드 추가
    hideDataSourceWarning() {
        const warnings = document.querySelectorAll('[id^="data-source-warning-"]');
        warnings.forEach(warning => warning.remove());
    }
    
    // 계좌 잔고 업데이트 메서드 수정
    updateAccountBalance(data) {
        console.log('🔍 [DEBUG] updateAccountBalance 시작, data:', data);
        
        const totalAssets = document.getElementById('totalAssets');
        const totalProfitLoss = document.getElementById('totalProfitLoss');
        const profitRate = document.getElementById('profitRate');
        
        console.log('🔍 [DEBUG] DOM 요소들:', {
            totalAssets: totalAssets ? 'found' : 'NOT FOUND',
            totalProfitLoss: totalProfitLoss ? 'found' : 'NOT FOUND', 
            profitRate: profitRate ? 'found' : 'NOT FOUND'
        });
        
        if (totalAssets) {
            const assets = parseInt(data.aset_evlt_amt || 0);
            const formattedAssets = this.formatPrice(assets) + '원';
            console.log('🔍 [DEBUG] 총자산 업데이트:', formattedAssets);
            totalAssets.textContent = formattedAssets;
        }
        
        if (totalProfitLoss) {
            const profit = parseInt(data.lspft || 0);
            const formattedProfit = this.formatPriceDiff(profit) + '원';
            console.log('🔍 [DEBUG] 평가손익 업데이트:', formattedProfit);
            totalProfitLoss.textContent = formattedProfit;
            totalProfitLoss.className = this.getPriceClass(profit);
        }
        
        if (profitRate) {
            const rate = parseFloat(data.lspft_rt || 0);
            const formattedRate = this.formatChangeRate(rate) + '%';
            console.log('🔍 [DEBUG] 수익률 업데이트:', formattedRate);
            profitRate.textContent = formattedRate;
            profitRate.className = this.getPriceClass(rate);
        }
        
        console.log('🔍 [DEBUG] updateAccountBalance 완료');
    }

    // 보유 종목 업데이트 메서드 수정
    updateHoldings(data) {
        console.log('🔍 [DEBUG] updateHoldings 시작, data:', data);
        
        const container = document.getElementById('holdingsList');
        console.log('🔍 [DEBUG] holdingsList 컨테이너:', container ? 'found' : 'NOT FOUND');
        
        if (!container) {
            console.error('🔍 [DEBUG] holdingsList 요소를 찾을 수 없습니다!');
            return;
        }
        
        if (!data.stk_acnt_evlt_prst || data.stk_acnt_evlt_prst.length === 0) {
            console.log('🔍 [DEBUG] 보유종목 데이터가 없음');
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-chart-pie fa-2x mb-2"></i>
                    <p>보유 종목이 없습니다.</p>
                </div>
            `;
            return;
        }
        
        console.log('🔍 [DEBUG] 보유종목 개수:', data.stk_acnt_evlt_prst.length);
        
        const holdingsHtml = data.stk_acnt_evlt_prst.map(holding => {
            const currentPrice = parseInt(holding.cur_prc || 0);
            const avgPrice = parseInt(holding.avg_prc || 0);
            const quantity = parseInt(holding.rmnd_qty || 0);
            const profitLoss = parseInt(holding.pl_amt || 0);
            const profitRate = parseFloat(holding.pl_rt || 0);
            const evaluationAmount = parseInt(holding.evlt_amt || 0);
            
            return `
                <div class="border-bottom py-2">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <h6 class="mb-1">${holding.stk_nm} (${holding.stk_cd})</h6>
                            <small class="text-muted">${quantity}주 보유</small>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold">${this.formatPrice(currentPrice)}원</div>
                            <div class="${this.getPriceClass(profitRate)} small">
                                ${this.formatPriceDiff(profitLoss)}원 (${this.formatChangeRate(profitRate)}%)
                            </div>
                        </div>
                    </div>
                    <div class="row mt-2 small text-muted">
                        <div class="col-6">평균단가: ${this.formatPrice(avgPrice)}원</div>
                        <div class="col-6 text-end">평가금액: ${this.formatPrice(evaluationAmount)}원</div>
                    </div>
                    <div class="row small">
                        <div class="col-12 text-end ${this.getPriceClass(profitLoss)}">
                            평가손익: ${this.formatPriceDiff(profitLoss)}원
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = holdingsHtml;
        console.log('🔍 [DEBUG] 보유종목 HTML 업데이트 완료');
    }

    // 거래 내역 업데이트 메서드
    updateTradingHistory(data) {
        const container = document.getElementById('tradingHistoryList');
        if (!container) return;
        
        if (!data.history || data.history.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-history fa-2x mb-2"></i>
                    <p>거래 내역이 없습니다.</p>
                </div>
            `;
            return;
        }
        
        const historyHtml = data.history.map(trade => {
            const typeClass = trade.trade_type === '매수' ? 'text-danger' : 'text-primary';
            
            return `
                <div class="trade-item border-bottom pb-2 mb-2">
                    <div class="row align-items-center">
                        <div class="col-md-2">
                            <small class="text-muted">${trade.trade_date}</small>
                        </div>
                        <div class="col-md-2">
                            <span class="badge ${trade.trade_type === '매수' ? 'bg-danger' : 'bg-primary'}">
                                ${trade.trade_type}
                            </span>
                        </div>
                        <div class="col-md-3">
                            <div class="fw-bold">${trade.stock_name}</div>
                            <small class="text-muted">${trade.stock_code}</small>
                        </div>
                        <div class="col-md-2 text-end">
                            <div>${trade.quantity}주</div>
                        </div>
                        <div class="col-md-2 text-end">
                            <div>${this.formatPrice(trade.price)}원</div>
                        </div>
                        <div class="col-md-1 text-end">
                            <div class="fw-bold">${this.formatPrice(trade.amount)}원</div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = historyHtml;
    }

    // 계좌 오류 표시 메서드
    showAccountError(message) {
        const accountContent = document.getElementById('accountContent');
        if (accountContent) {
            accountContent.innerHTML = `
                <div class="alert alert-danger" role="alert">
                    <i class="fas fa-exclamation-triangle me-2"></i>
                    ${message}
                </div>
            `;
        }
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
                const isEnabled = !!condition.is_enabled;
                return `
                    <div class="list-group-item condition-item" data-condition-id="${condition.id}" data-condition-name="${condition.condition_name}">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="mb-1">${condition.condition_name || '이름 없음'}</h6>
                                <small class="text-muted">ID: ${condition.id}</small>
                            </div>
                            <div class="d-flex align-items-center gap-2">
                                <span class="badge ${isEnabled ? 'bg-danger' : 'bg-secondary'} rounded-pill">
                                    ${isEnabled ? '자동매매' : '비활성'}
                                </span>
                                <div class="form-check form-switch m-0">
                                    <input class="form-check-input cond-toggle" type="checkbox" ${isEnabled ? 'checked' : ''} data-condition-name="${condition.condition_name}">
                                </div>
                            </div>
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
                    // 스위치 클릭은 상위 선택으로 전파하지 않음
                    if (e.target && e.target.classList && e.target.classList.contains('cond-toggle')) {
                        return;
                    }
                    const conditionId = e.currentTarget.dataset.conditionId;
                    this.selectCondition(conditionId, e.currentTarget);
                });
            });

            // 토글 스위치 이벤트 바인딩
            const toggles = container.querySelectorAll('.cond-toggle');
            toggles.forEach(toggle => {
                toggle.addEventListener('click', evt => evt.stopPropagation());
                toggle.addEventListener('change', async (evt) => {
                    const conditionName = evt.currentTarget.dataset.conditionName;
                    const enabled = evt.currentTarget.checked;
                    await this.toggleAutoTradeCondition(conditionName, enabled, evt.currentTarget.closest('.condition-item'));
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

    async toggleAutoTradeCondition(conditionName, enabled, itemEl) {
        try {
            const res = await fetch('/conditions/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ condition_name: conditionName, is_enabled: enabled })
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            // UI 배지 업데이트
            if (itemEl) {
                const badge = itemEl.querySelector('.badge');
                if (badge) {
                    badge.classList.toggle('bg-danger', enabled);
                    badge.classList.toggle('bg-secondary', !enabled);
                    badge.textContent = enabled ? '자동매매' : '비활성';
                }
            }
        } catch (e) {
            console.error('자동매매 토글 실패:', e);
            alert('자동매매 상태 변경에 실패했습니다.');
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
        this.selectedConditionName = element.dataset.conditionName || null;

        // UI 업데이트
        document.getElementById('refreshStocks').disabled = false;

        // 주식 정보 로딩
        this.loadStocks(conditionId);
    }

    async loadStocks(conditionId) {
        try {
            this.showStocksLoading();
            let url = `/conditions/${conditionId}/stocks`;
            if (this.selectedConditionName) {
                url += `?condition_name=${encodeURIComponent(this.selectedConditionName)}`;
            }
            const response = await fetch(url);
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
                                    onclick="return window.showStockChartHandler(event, '${stock.stock_code}', '${stock.stock_name}')">
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
                console.log('🔍 [DEBUG] 초기 모니터링 상태 확인 시작');
                const response = await fetch('/monitoring/status');
                const data = await response.json();
                console.log('🔍 [DEBUG] 초기 상태 API 응답:', data);
                
                // API 응답 구조에 맞게 수정
                const isRunning = data.monitoring?.is_running || data.is_running || data.is_monitoring;
                console.log('🔍 [DEBUG] 초기 상태 - isRunning:', isRunning);
                console.log('🔍 [DEBUG] monitoring 객체:', data.monitoring);
                
                // UI만 현재 상태에 맞게 업데이트 (자동 시작하지 않음)
                this.updateMonitoringUI(isRunning);
            } catch (error) {
                console.error('🔍 [DEBUG] 모니터링 상태 확인 실패:', error);
                // 오류 시 기본적으로 중지 상태로 표시
                this.updateMonitoringUI(false);
            }
        }

        // 모니터링 상태를 원하는 값이 될 때까지 일정 시간 폴링
        async pollMonitoring(desiredRunning, timeoutMs = 30000, intervalMs = 2000) {
            const end = Date.now() + timeoutMs;
            while (Date.now() < end) {
                try {
                    const res = await fetch('/monitoring/status');
                    const data = await res.json().catch(() => ({}));
                    const isRunning = !!(data.monitoring?.is_running || data.is_running || data.is_monitoring);
                    this.updateMonitoringUI(isRunning);
                    if (isRunning === desiredRunning) {
                        return true;
                    }
                } catch (e) {
                    console.error('🔁 상태 확인 실패:', e);
                }
                await new Promise(r => setTimeout(r, intervalMs));
            }
            return false;
        }
        
        updateMonitoringUI(isMonitoring) {
            console.log('🔍 [DEBUG] updateMonitoringUI 호출됨 - isMonitoring:', isMonitoring);
            const button = document.getElementById('toggleMonitoring');
            const textSpan = document.getElementById('monitoringText');
            const iconEl = button ? button.querySelector('i') : null;
            
            if (button) {
                // 클래스 업데이트: 크기 유지하며 색상만 토글
                button.className = `btn btn-sm ${isMonitoring ? 'btn-danger' : 'btn-success'}`;
            }
            if (iconEl) {
                // 어떤 상태든 스피너는 제거
                iconEl.classList.remove('fa-spinner', 'fa-spin');
                iconEl.classList.remove('fa-play', 'fa-stop');
                iconEl.classList.add(isMonitoring ? 'fa-stop' : 'fa-play');
            }
            if (textSpan) {
                textSpan.textContent = isMonitoring ? '모니터링 중지' : '모니터링 시작';
            }
            if (!button) {
                console.error('🔍 [DEBUG] toggleMonitoring 버튼을 찾을 수 없습니다!');
            }
        }
        
        async toggleMonitoring() {
            try {
                // 현재 모니터링 상태 확인
                const statusResponse = await fetch('/monitoring/status');
                const statusData = await statusResponse.json();
                const isCurrentlyRunning = statusData.monitoring?.is_running || statusData.is_running || statusData.is_monitoring;
                
                console.log('🔍 [DEBUG] 현재 모니터링 상태:', isCurrentlyRunning);
                
                // 상태에 따라 시작 또는 중지
                const endpoint = isCurrentlyRunning ? '/monitoring/stop' : '/monitoring/start';
                const action = isCurrentlyRunning ? '중지' : '시작';
                
                console.log(`🔍 [DEBUG] 모니터링 ${action} 요청:`, endpoint);
                // 버튼 비활성화 및 로딩 표시
                const button = document.getElementById('toggleMonitoring');
                const textSpan = document.getElementById('monitoringText');
                const iconEl = button ? button.querySelector('i') : null;
                if (button) button.disabled = true;
                if (iconEl) {
                    iconEl.classList.remove('fa-play', 'fa-stop');
                    iconEl.classList.add('fa-spinner', 'fa-spin');
                }
                if (textSpan) textSpan.textContent = `모니터링 ${action} 중...`;
                // 타임아웃이 있는 요청으로 전환 (네트워크 지연 시 UI 고정 방지)
                const response = await this.fetchWithTimeout(endpoint, { method: 'POST' }, 30000);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                console.log('🔍 [DEBUG] API 응답:', data);
                const isRunning = data.monitoring?.is_running || data.is_running || data.is_monitoring;
                // 응답 즉시 UI를 낙관적으로 갱신
                this.updateMonitoringUI(isRunning);
                // 백엔드 반영 지연 대비 폴링으로 확정
                await this.pollMonitoring(!isCurrentlyRunning, 30000, 2000);
                
                // 버튼 활성화 및 아이콘 복원
                if (button) button.disabled = false;
                if (iconEl) iconEl.classList.remove('fa-spinner', 'fa-spin');
                
            } catch (error) {
                console.error('🔍 [DEBUG] 모니터링 토글 실패:', error);
                const isAbort = (error && (error.name === 'AbortError' || /aborted/i.test(String(error.message))));
                const message = isAbort ? '요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.' : ('모니터링 상태 변경에 실패했습니다: ' + error.message);
                alert(message);
                // 실패 시 버튼/아이콘 복원 시도
                const button = document.getElementById('toggleMonitoring');
                const textSpan = document.getElementById('monitoringText');
                const iconEl = button ? button.querySelector('i') : null;
                if (button) button.disabled = false;
                if (iconEl) {
                    iconEl.classList.remove('fa-spinner', 'fa-spin');
                    iconEl.classList.add('fa-play');
                }
                if (textSpan) textSpan.textContent = '모니터링 시작';
            } finally {
                // 어떤 경우에도 스피너 정리 및 최종 상태 동기화 시도
                try {
                    const button = document.getElementById('toggleMonitoring');
                    const textSpan = document.getElementById('monitoringText');
                    const iconEl = button ? button.querySelector('i') : null;
                    if (button) button.disabled = false;
                    if (iconEl) iconEl.classList.remove('fa-spinner', 'fa-spin');
                    // 서버 상태로 최종 동기화
                    const res = await fetch('/monitoring/status');
                    const st = await res.json().catch(() => ({}));
                    const on = !!(st.monitoring?.is_running || st.is_running || st.is_monitoring);
                    this.updateMonitoringUI(on);
                    if (textSpan && !on) textSpan.textContent = '모니터링 시작';
                } catch (syncErr) {
                    console.error('🔁 최종 상태 동기화 실패:', syncErr);
                }
            }
        }

        // fetch 타임아웃 헬퍼
        async fetchWithTimeout(url, options = {}, timeoutMs = 30000) {
            const controller = new AbortController();
            const id = setTimeout(() => controller.abort(), timeoutMs);
            try {
                const resp = await fetch(url, { ...options, signal: controller.signal });
                return resp;
            } finally {
                clearTimeout(id);
            }
        }
        
        startAutoRefresh() {
            // 종목 탭에서만 자동 새로고침 실행
            if (this.currentTab !== 'stock') return;
            
            // 30초마다 자동 새로고침
            this.refreshInterval = setInterval(() => {
                if (this.selectedConditionId && this.currentTab === 'stock') {
                    this.loadStocks(this.selectedConditionId);
                }
                // 매수대기 목록도 함께 새로고침
                console.log('🔁 [PENDING] auto refresh tick');
                this.loadPendingSignals();
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
            console.log('종목 선택:', stockCode, stockName);
            
            // 선택된 종목을 헤더에 표시
            this.updateSelectedStockDisplay(stockCode, stockName);
            
            // 종목 선택 시각적 피드백
            this.updateStockSelection(stockCode);
            
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

        // 선택된 종목을 헤더에 표시하는 함수
        updateSelectedStockDisplay(stockCode, stockName) {
            const selectedStockInfo = document.getElementById('selectedStockInfo');
            const selectedStockName = document.getElementById('selectedStockName');
            const selectedStockCode = document.getElementById('selectedStockCode');
            
            if (selectedStockInfo && selectedStockName && selectedStockCode) {
                selectedStockName.textContent = stockName;
                selectedStockCode.textContent = `(${stockCode})`;
                selectedStockInfo.style.display = 'block';
            }
        }

        // 종목 선택 시각적 피드백
        updateStockSelection(selectedStockCode) {
            // 모든 종목 아이템에서 선택 상태 제거
            const allStockItems = document.querySelectorAll('.stock-item');
            allStockItems.forEach(item => {
                item.classList.remove('selected');
            });
            
            // 선택된 종목에 선택 상태 추가
            const selectedItem = document.querySelector(`[data-stock-code="${selectedStockCode}"]`);
            if (selectedItem) {
                selectedItem.classList.add('selected');
            }
        }

        // 선택 해제 함수
        clearStockSelection() {
            this.selectedStockForNews = null;
            
            // 헤더에서 선택된 종목 정보 숨기기
            const selectedStockInfo = document.getElementById('selectedStockInfo');
            if (selectedStockInfo) {
                selectedStockInfo.style.display = 'none';
            }
            
            // 모든 종목 아이템에서 선택 상태 제거
            const allStockItems = document.querySelectorAll('.stock-item');
            allStockItems.forEach(item => {
                item.classList.remove('selected');
            });
            
            // 뉴스 섹션 초기화
            const newsContent = document.getElementById('newsContent');
            if (newsContent) {
                newsContent.innerHTML = `
                    <div class="text-center text-muted py-3">
                        <i class="fas fa-newspaper fa-2x mb-2"></i>
                        <p class="mb-0">종목을 클릭하여 관련 뉴스를 확인하세요.</p>
                    </div>
                `;
            }
        }
        
        // 새로운 뉴스 로딩 함수 추가 (뉴스 + 토론 글)
        async loadNews(stockCode, stockName) {
            try {
                console.log('🔍 [DEBUG] 종목 정보 로딩 시작:', stockCode, stockName);
                
                // 뉴스와 토론 글을 함께 가져오는 새로운 API 사용
                const response = await fetch(`/stocks/${stockCode}/info?stock_name=${encodeURIComponent(stockName)}`);
                const data = await response.json();

                const newsContent = document.getElementById('newsContent');
                if (!newsContent) return;

                console.log('🔍 [DEBUG] API 응답:', data);

                // 뉴스 섹션
                let newsHtml = '';
                if (data.news && data.news.items && data.news.items.length > 0) {
                    newsHtml += `
                        <div class="mb-4">
                            <h5 class="text-primary mb-3">
                                <i class="fas fa-newspaper me-2"></i>뉴스 (${data.news.items.length}개)
                            </h5>
                            <div class="news-section">
                                ${data.news.items.map(item => `
                                    <div class="news-item border-bottom pb-3 mb-3">
                                        <h6 class="news-title">
                                            <a href="${item.link}" target="_blank" class="text-decoration-none">
                                                ${item.title}
                                            </a>
                                        </h6>
                                        <p class="news-description text-muted mb-2">${item.description}</p>
                                        <div class="news-meta">
                                            <small class="text-muted">
                                                <i class="fas fa-calendar-alt me-1"></i>${item.pubDate || ''}
                                            </small>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `;
                } else {
                    newsHtml += `
                        <div class="mb-4">
                            <h5 class="text-primary mb-3">
                                <i class="fas fa-newspaper me-2"></i>뉴스
                            </h5>
                            <div class="text-center text-muted py-3">
                                <i class="fas fa-newspaper fa-2x mb-2"></i>
                                <p>관련 뉴스가 없습니다.</p>
                            </div>
                        </div>
                    `;
                }

                // 토론 글 섹션
                if (data.discussions && data.discussions.discussions && data.discussions.discussions.length > 0) {
                    newsHtml += `
                        <div class="mb-4">
                            <h5 class="text-success mb-3">
                                <i class="fas fa-comments me-2"></i>종목토론 (${data.discussions.discussions.length}개)
                            </h5>
                            <div class="discussions-section">
                                ${data.discussions.discussions.map(discussion => `
                                    <div class="discussion-item border-bottom pb-2 mb-2">
                                        <div class="discussion-title">
                                            <a href="https://finance.naver.com/item/board.nhn?code=${stockCode}" target="_blank" class="text-decoration-none">
                                                ${discussion.title}
                                            </a>
                                        </div>
                                        <div class="discussion-meta">
                                            <small class="text-muted">
                                                <i class="fas fa-user me-1"></i>${discussion.author || '익명'}
                                                <i class="fas fa-clock me-1 ms-2"></i>${discussion.date || ''}
                                            </small>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `;
                } else {
                    newsHtml += `
                        <div class="mb-4">
                            <h5 class="text-success mb-3">
                                <i class="fas fa-comments me-2"></i>종목토론
                            </h5>
                            <div class="text-center text-muted py-3">
                                <i class="fas fa-comments fa-2x mb-2"></i>
                                <p>오늘의 토론 글이 없습니다.</p>
                            </div>
                        </div>
                    `;
                }

                newsContent.innerHTML = newsHtml;
                console.log('🔍 [DEBUG] 종목 정보 로딩 완료');
                
            } catch (error) {
                console.error('🔍 [DEBUG] 종목 정보 로딩 오류:', error);
                const newsContent = document.getElementById('newsContent');
                if (newsContent) {
                    newsContent.innerHTML = `
                        <div class="text-center text-muted py-4">
                            <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                            <p>정보를 불러오는 중 오류가 발생했습니다.</p>
                        </div>
                    `;
                }
            }
        }
        
        showStockChart(stockCode, stockName) {
            console.log('차트 표시:', stockCode, stockName);
            showChart(stockCode, stockName);
        }

        // 계좌 정보 업데이트 메서드 (클래스 내부로 이동)
        updateAccountInfo(data) {
            console.log('🔍 [DEBUG] updateAccountInfo 시작, data:', data);
            
            // 계좌 기본 정보 업데이트
            const accountName = document.getElementById('accountName');
            const branchName = document.getElementById('branchName');
            const deposit = document.getElementById('deposit');
            const availableCash = document.getElementById('availableCash');
            
            console.log('🔍 [DEBUG] 계좌정보 DOM 요소들:', {
                accountName: accountName ? 'found' : 'NOT FOUND',
                branchName: branchName ? 'found' : 'NOT FOUND',
                deposit: deposit ? 'found' : 'NOT FOUND',
                availableCash: availableCash ? 'found' : 'NOT FOUND'
            });
            
            if (accountName) {
                accountName.textContent = data.acnt_nm || '계좌명 없음';
            }
            
            if (branchName) {
                branchName.textContent = data.brch_nm || '지점명 없음';
            }
            
            if (deposit) {
                const formattedDeposit = this.formatPrice(parseInt(data.entr || 0)) + '원';
                deposit.textContent = formattedDeposit;
            }
            
            if (availableCash) {
                const formattedCash = this.formatPrice(parseInt(data.d2_entra || 0)) + '원';
                availableCash.textContent = formattedCash;
            }
            
            console.log('🔍 [DEBUG] updateAccountInfo 완료');
        }

    async loadTradingSettings() {
        try {
            console.log('🔍 [TRADING] Loading trading settings...');
            const response = await fetch('/trading/settings');
            const data = await response.json();
            console.log('🔍 [TRADING] Settings loaded:', data);

            // UI 업데이트
            const autoToggle = document.getElementById('autoTradingToggle');
            const maxInvest = document.getElementById('maxInvestAmount');
            const stopLoss = document.getElementById('stopLossRate');
            const takeProfit = document.getElementById('takeProfitRate');

            if (autoToggle) autoToggle.checked = data.is_enabled;
            if (maxInvest) maxInvest.value = data.max_invest_amount;
            if (stopLoss) stopLoss.value = data.stop_loss_rate;
            if (takeProfit) takeProfit.value = data.take_profit_rate;

            console.log('🔍 [TRADING] UI updated with settings');
        } catch (error) {
            console.error('🔍 [TRADING] Failed to load trading settings:', error);
        }
    }

    async saveTradingSettings() {
        try {
            console.log('🔍 [TRADING] Saving trading settings...');
            
            const settings = {
                is_enabled: document.getElementById('autoTradingToggle').checked,
                max_invest_amount: parseInt(document.getElementById('maxInvestAmount').value) || 1000000,
                stop_loss_rate: parseInt(document.getElementById('stopLossRate').value) || 5,
                take_profit_rate: parseInt(document.getElementById('takeProfitRate').value) || 10
            };

            console.log('🔍 [TRADING] Settings to save:', settings);

            const response = await fetch('/trading/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });

            if (response.ok) {
                const result = await response.json();
                console.log('🔍 [TRADING] Settings saved:', result);
                alert('자동매매 설정이 저장되었습니다.');
            } else {
                throw new Error(`HTTP ${response.status}`);
            }
        } catch (error) {
            console.error('🔍 [TRADING] Failed to save trading settings:', error);
            alert('설정 저장에 실패했습니다: ' + error.message);
        }
    }
}

// 앱 초기화
document.addEventListener('DOMContentLoaded', () => {
    window.app = new StockMonitorApp();
    // 전역 클릭 핸들러 바인딩
    window.showStockChartHandler = async (evt, code, name) => {
        try {
            if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
            if (!window.app || typeof window.app.showStockChart !== 'function') {
                console.warn('app.showStockChart가 없습니다. 폴백 showChart 사용');
                if (typeof showChart === 'function') showChart(code, name);
                return false;
            }
            window.app.showStockChart(code, name);
        } catch (e) {
            console.error('차트 핸들러 오류:', e);
        }
        return false;
    };
});

window.addEventListener('beforeunload', () => {
    if (window.app) {
        window.app.stopAutoRefresh();
    }
});

// 차트 관련 함수들
async function loadChartImage(stockCode, period = '1M') {
    try {
        const response = await fetch(`/chart/image/${stockCode}?period=${encodeURIComponent(period)}`);
        if (response.ok) {
            const data = await response.json();
            if (data && data.image) {
                return data.image;
            }
        }
    } catch (error) {
        console.error('차트 이미지 로드 실패:', error);
    }
    return null;
}

function showChart(stockCode, stockName) {
    const modalEl = document.getElementById('chartModal');
    const chartTitle = document.getElementById('chartStockName');
    const chartCode = document.getElementById('chartStockCode');
    const chartContainer = document.getElementById('chartContainer');

    if (chartTitle) chartTitle.textContent = stockName || '';
    if (chartCode) chartCode.textContent = stockCode || '';

    const periodRadios = document.querySelectorAll('input[name="chartPeriod"]');
    let currentPeriod = '1M';
    periodRadios.forEach(r => {
        if (r.checked) currentPeriod = r.value;
        r.onchange = async () => {
            currentPeriod = r.value;
            await renderChartImage(stockCode, currentPeriod, chartContainer);
        };
    });

    renderChartImage(stockCode, currentPeriod, chartContainer);

    if (window.bootstrap && typeof bootstrap.Modal === 'function') {
        const bsModal = new bootstrap.Modal(modalEl);
        bsModal.show();
    } else {
        // 폴백: 부트스트랩이 없을 경우 단순 표시
        modalEl.style.display = 'block';
        modalEl.classList.add('show');
    }
}

async function renderChartImage(stockCode, period, containerEl) {
    if (!containerEl) return;
    containerEl.innerHTML = `
        <div class="text-center p-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">차트 로딩 중...</span>
            </div>
            <p class="mt-2 mb-0">차트를 불러오는 중...</p>
        </div>
    `;
    const imageUrl = await loadChartImage(stockCode, period);
    if (imageUrl) {
        containerEl.innerHTML = `<img src="${imageUrl}" alt="${stockCode} 차트" style="max-width: 100%; height: auto;" />`;
    } else {
        containerEl.innerHTML = `<div class="text-center text-muted py-4">차트를 불러오지 못했습니다.</div>`;
    }
}

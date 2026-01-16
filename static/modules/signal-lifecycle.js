// 시그널 라이프사이클 추적 모듈

class SignalLifecycleTracker {
    constructor() {
        this.signals = [];
        this.currentFilter = 'all';
        this.autoRefreshInterval = null;
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.loadSignals();
        this.startAutoRefresh();
    }

    setupEventListeners() {
        // 새로고침 버튼
        document.getElementById('refreshBtn').addEventListener('click', () => {
            this.loadSignals();
        });

        // 필터 버튼
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.currentFilter = e.target.dataset.filter;
                this.renderSignals();
                
                // 실패 필터 선택 시 정리 버튼 표시
                const cleanupBtn = document.getElementById('cleanupFailedBtn');
                if (cleanupBtn) {
                    cleanupBtn.style.display = this.currentFilter === 'failed' ? 'inline-block' : 'none';
                }
            });
        });

        // 실패건 정리 버튼
        const cleanupBtn = document.getElementById('cleanupFailedBtn');
        if (cleanupBtn) {
            cleanupBtn.addEventListener('click', () => {
                this.cleanupFailedSignals();
            });
        }

        // 자동 새로고침 토글
        document.getElementById('autoRefresh').addEventListener('change', (e) => {
            if (e.target.checked) {
                this.startAutoRefresh();
            } else {
                this.stopAutoRefresh();
            }
        });
    }

    async loadSignals() {
        const btn = document.getElementById('refreshBtn');
        const icon = btn.querySelector('i');
        icon.classList.add('rotating');

        try {
            // 시그널 및 포지션 데이터 동시 로드
            // skip_price=true로 API 호출 최소화 (Position의 current_price 사용)
            const [signalsResponse, positionsResponse] = await Promise.all([
                fetch('/signals/pending?status=ALL&skip_price=true'),
                fetch('/positions/?status=ALL')
            ]);

            if (!signalsResponse.ok || !positionsResponse.ok) {
                throw new Error('API 응답 오류');
            }

            const signalsData = await signalsResponse.json();
            const positionsData = await positionsResponse.json();

            // 응답 형식 처리 (배열 또는 객체)
            const signals = Array.isArray(signalsData) ? signalsData : (signalsData.items || []);
            const positions = Array.isArray(positionsData) ? positionsData : (positionsData.items || []);

            // 데이터 결합 및 라이프사이클 상태 계산
            this.signals = this.processSignals(signals, positions);
            
            this.renderSignals();
            this.updateStats();
            this.updateLastUpdateTime();

        } catch (error) {
            console.error('시그널 로드 실패:', error);
            console.error('Error details:', error.message, error.stack);
            this.showError(`데이터를 불러오는 중 오류가 발생했습니다: ${error.message}`);
        } finally {
            icon.classList.remove('rotating');
        }
    }

    processSignals(signals, positions) {
        // 포지션 맵 생성 (signal_id 기준)
        const positionMap = {};
        positions.forEach(pos => {
            if (pos.signal_id) {
                positionMap[pos.signal_id] = pos;
            }
        });

        return signals.map(signal => {
            // ✅ 우선순위: 1) signal에 이미 포함된 position, 2) positions 배열에서 찾은 것
            const position = signal.position || positionMap[signal.id];
            
            return {
                ...signal,
                lifecycle: this.calculateLifecycle(signal, position),
                position: position
            };
        }).sort((a, b) => new Date(b.detected_at) - new Date(a.detected_at));
    }

    calculateLifecycle(signal, position) {
        // 기준 시간 (신호 감지 시간)
        const baseTime = new Date(signal.detected_at);
        
        // 각 단계별 예상 시간 간격 (초)
        const addSeconds = (date, seconds) => {
            const newDate = new Date(date);
            newDate.setSeconds(newDate.getSeconds() + seconds);
            return newDate.toISOString();
        };
        
        const stages = {
            detected: { 
                status: 'completed', 
                time: signal.detected_at,
                label: '시그널 포착',
                icon: 'fa-radar'
            },
            priceCheck: { 
                status: 'unknown', 
                time: addSeconds(baseTime, 2),  // 2초 후
                label: '현재가 조회',
                icon: 'fa-dollar-sign'
            },
            quantityCalc: { 
                status: 'unknown', 
                time: addSeconds(baseTime, 4),  // 4초 후
                label: '수량 계산',
                icon: 'fa-calculator'
            },
            orderPlaced: { 
                status: 'unknown', 
                time: addSeconds(baseTime, 6),  // 6초 후
                label: '주문 실행',
                icon: 'fa-paper-plane'
            },
            orderCompleted: { 
                status: 'unknown', 
                time: addSeconds(baseTime, 10),  // 10초 후
                label: '주문 완료',
                icon: 'fa-check-circle'
            },
            positionCreated: { 
                status: 'unknown', 
                time: position ? position.created_at : addSeconds(baseTime, 12),  // 포지션 생성 시간 또는 12초 후
                label: '포지션 생성',
                icon: 'fa-briefcase'
            }
        };

        // 상태에 따라 단계 업데이트
        if (signal.status === 'PENDING') {
            stages.priceCheck.status = 'active';
        } else if (signal.status === 'PROCESSING') {
            stages.priceCheck.status = 'completed';
            stages.quantityCalc.status = 'completed';
            stages.orderPlaced.status = 'active';
        } else if (signal.status === 'ORDERED') {
            stages.priceCheck.status = 'completed';
            stages.quantityCalc.status = 'completed';
            stages.orderPlaced.status = 'completed';
            stages.orderCompleted.status = 'completed';
            
            if (position) {
                stages.positionCreated.status = 'completed';
                stages.positionCreated.time = position.created_at;
            } else {
                stages.positionCreated.status = 'active';
            }
        } else if (signal.status === 'FAILED') {
            // 실패한 단계 찾기
            if (signal.failure_reason) {
                if (signal.failure_reason.includes('현재가')) {
                    stages.priceCheck.status = 'failed';
                } else if (signal.failure_reason.includes('수량') || signal.failure_reason.includes('예수금')) {
                    stages.priceCheck.status = 'completed';
                    stages.quantityCalc.status = 'failed';
                } else if (signal.failure_reason.includes('주문')) {
                    stages.priceCheck.status = 'completed';
                    stages.quantityCalc.status = 'completed';
                    stages.orderPlaced.status = 'failed';
                } else {
                    stages.orderPlaced.status = 'failed';
                }
            }
        } else if (signal.status === 'CANCELED') {
            stages.priceCheck.status = 'canceled';
        }

        return stages;
    }

    renderSignals() {
        const container = document.getElementById('signalList');
        const emptyState = document.getElementById('emptyState');

        // 필터링
        let filteredSignals = this.signals;
        if (this.currentFilter !== 'all') {
            filteredSignals = this.signals.filter(s => {
                if (this.currentFilter === 'pending') return s.status === 'PENDING';
                if (this.currentFilter === 'processing') return s.status === 'PROCESSING';
                if (this.currentFilter === 'ordered') return s.status === 'ORDERED';
                if (this.currentFilter === 'failed') return s.status === 'FAILED';
                return true;
            });
        }

        if (filteredSignals.length === 0) {
            container.innerHTML = '';
            emptyState.style.display = 'block';
            return;
        }

        emptyState.style.display = 'none';
        container.innerHTML = filteredSignals.map(signal => this.renderSignalCard(signal)).join('');
    }

    renderSignalCard(signal) {
        const lifecycle = signal.lifecycle;
        const stageKeys = Object.keys(lifecycle);
        const completedCount = stageKeys.filter(key => lifecycle[key].status === 'completed').length;
        const progress = (completedCount / stageKeys.length) * 100;

        return `
            <div class="signal-card" data-signal-id="${signal.id}">
                <div class="signal-header">
                    <div class="stock-info">
                        <div>
                            <div class="stock-name">${signal.stock_name}</div>
                            <div class="stock-code">${signal.stock_code}</div>
                        </div>
                        <span class="badge-status badge-${signal.status.toLowerCase()}">
                            ${this.getStatusText(signal.status)}
                        </span>
                    </div>
                    <div class="text-end">
                        <div class="signal-time">
                            <i class="far fa-clock me-1"></i>
                            ${this.formatTime(signal.detected_at)}
                        </div>
                        <div class="small text-muted mt-1">
                            조건식 #${signal.condition_id} | 타입: ${signal.signal_type}
                        </div>
                    </div>
                </div>

                <div class="lifecycle-timeline">
                    <div class="timeline-line">
                        <div class="timeline-progress" style="width: ${progress}%"></div>
                    </div>
                    <div class="timeline-steps">
                        ${stageKeys.map(key => this.renderStage(lifecycle[key])).join('')}
                    </div>
                </div>

                ${signal.status === 'FAILED' && signal.failure_reason ? `
                    <div class="error-message">
                        <i class="fas fa-exclamation-triangle"></i>
                        <strong>실패 이유:</strong> ${signal.failure_reason}
                    </div>
                ` : ''}

                ${this.renderDetails(signal)}
            </div>
        `;
    }

    renderStage(stage) {
        const statusClass = stage.status === 'completed' ? 'completed' : 
                          stage.status === 'active' ? 'active' : 
                          stage.status === 'failed' ? 'failed' : '';

        const statusBadge = stage.status === 'completed' ? '<span class="step-status success">완료</span>' :
                           stage.status === 'active' ? '<span class="step-status processing">진행중</span>' :
                           stage.status === 'failed' ? '<span class="step-status error">실패</span>' : '';

        return `
            <div class="timeline-step">
                <div class="step-icon ${statusClass}">
                    <i class="fas ${stage.icon}"></i>
                </div>
                <div class="step-label">${stage.label}</div>
                ${stage.time ? `<div class="step-time">${this.formatTime(stage.time, true)}</div>` : ''}
                ${statusBadge}
            </div>
        `;
    }

    renderDetails(signal) {
        const details = [];

        details.push({ label: 'ID', value: `#${signal.id}` });
        
        // 🔍 디버깅: Position 데이터 확인
        console.log(`[DEBUG] Signal #${signal.id} (${signal.stock_name}):`, {
            hasPosition: !!signal.position,
            status: signal.status,
            position: signal.position
        });
        
        // 상태 표시
        details.push({ 
            label: '상태', 
            value: `<span class="badge-${signal.status.toLowerCase()}">${this.getStatusText(signal.status)}</span>` 
        });
        
        // Position이 없는 경우 상태 메시지 표시
        if (!signal.position && signal.status === 'ORDERED') {
            details.push({ 
                label: '진행상태', 
                value: '<span style="color: #ff9800; font-weight: bold;">⏳ 주문 체결 대기 중...</span>' 
            });
            details.push({ 
                label: '안내', 
                value: '<span style="color: #666; font-size: 12px;">주문이 체결되면 현재가, 손절가, 목표가가 표시됩니다.</span>' 
            });
        }
        
        // 매수가 정보 (포지션이 있는 경우)
        if (signal.position) {
            details.push({ 
                label: '매수가', 
                value: `${signal.position.buy_price.toLocaleString()}원`,
                highlight: false
            });
            
            // 현재가 표시 (가장 중요한 정보)
            if (signal.position.current_price && signal.position.current_price > 0) {
                const currentPrice = signal.position.current_price;
                const buyPrice = signal.position.buy_price;
                const pnl = ((currentPrice - buyPrice) / buyPrice) * 100;
                const priceChange = currentPrice - buyPrice;
                const priceChangeStr = priceChange >= 0 ? `+${priceChange.toLocaleString()}` : priceChange.toLocaleString();
                const pnlClass = pnl >= 0 ? 'text-success' : 'text-danger';
                
                details.push({ 
                    label: '현재가', 
                    value: `<span class="${pnlClass}" style="font-weight: bold; font-size: 13px;">${currentPrice.toLocaleString()}원</span>`,
                    highlight: true
                });
                
                // 수익률 (현재가 바로 아래)
                details.push({ 
                    label: '수익률', 
                    value: `<span class="${pnlClass}" style="font-weight: bold;">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}% (${priceChangeStr}원)</span>`,
                    highlight: true
                });
            }
            
            // 목표가 (있는 경우)
            if (signal.target_price && signal.target_price > 0) {
                const targetPrice = signal.target_price;
                const buyPrice = signal.position.buy_price;
                const targetGain = ((targetPrice - buyPrice) / buyPrice) * 100;
                
                details.push({ 
                    label: '목표가', 
                    value: `${targetPrice.toLocaleString()}원 <span style="color: #0066cc; font-size: 11px;">(+${targetGain.toFixed(1)}%)</span>`,
                    highlight: false
                });
            }
            
            details.push({ label: '수량', value: `${signal.position.buy_quantity}주` });
            details.push({ label: '매수금액', value: `${signal.position.buy_amount.toLocaleString()}원` });
        } else {
            // 포지션이 없는 경우 (아직 주문 전)
            if (signal.target_price) {
                details.push({ 
                    label: '목표가', 
                    value: `${signal.target_price.toLocaleString()}원`,
                    highlight: false
                });
            }
            
            // 예상 투자금액이 있으면 표시
            if (signal.target_quantity && signal.target_price) {
                const estimatedAmount = signal.target_quantity * signal.target_price;
                details.push({ 
                    label: '예상금액', 
                    value: `${estimatedAmount.toLocaleString()}원 (${signal.target_quantity}주)` 
                });
            }
        }

        if (details.length === 0) return '';

        return `
            <div class="detail-section">
                <div class="detail-grid">
                    ${details.map(d => `
                        <div class="detail-item ${d.highlight ? 'detail-highlight' : ''}">
                            <div class="detail-label">${d.label}</div>
                            <div class="detail-value">${d.value}</div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    getStatusText(status) {
        const statusMap = {
            'PENDING': '대기중',
            'PROCESSING': '처리중',
            'ORDERED': '주문완료',
            'FAILED': '실패',
            'CANCELED': '취소됨'
        };
        return statusMap[status] || status;
    }

    formatTime(timeStr, shortFormat = false) {
        if (!timeStr) return '-';
        const date = new Date(timeStr);
        
        if (shortFormat) {
            return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
        }
        
        const now = new Date();
        const diff = now - date;
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (minutes < 1) return '방금 전';
        if (minutes < 60) return `${minutes}분 전`;
        if (hours < 24) return `${hours}시간 전`;
        if (days < 7) return `${days}일 전`;
        
        return date.toLocaleString('ko-KR');
    }

    updateStats() {
        document.getElementById('totalCount').textContent = this.signals.length;
    }

    updateLastUpdateTime() {
        const now = new Date();
        document.getElementById('lastUpdate').textContent = now.toLocaleTimeString('ko-KR');
    }

    startAutoRefresh() {
        if (this.autoRefreshInterval) return;
        
        this.autoRefreshInterval = setInterval(() => {
            if (document.getElementById('autoRefresh').checked) {
                this.loadSignals();
            }
        }, 60000); // 60초마다 (API 호출 제한 고려 - 키움: 1분당 20회)
    }

    stopAutoRefresh() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
            this.autoRefreshInterval = null;
        }
    }

    showError(message) {
        const container = document.getElementById('signalList');
        container.innerHTML = `
            <div class="alert alert-danger" role="alert">
                <i class="fas fa-exclamation-circle me-2"></i>
                ${message}
            </div>
        `;
    }

    async cleanupFailedSignals() {
        const cleanupBtn = document.getElementById('cleanupFailedBtn');
        const originalText = cleanupBtn.innerHTML;
        
        // 확인 다이얼로그
        if (!confirm('실패한 신호를 모두 정리하시겠습니까?\n(관련 Position이 있는 신호는 제외됩니다)')) {
            return;
        }
        
        try {
            cleanupBtn.disabled = true;
            cleanupBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 정리 중...';
            
            const response = await fetch('/signals/cleanup-failed', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (!response.ok) {
                throw new Error('정리 실패');
            }
            
            const data = await response.json();
            
            alert(`✅ ${data.message}\n\n삭제된 신호: ${data.deleted_count}개`);
            
            // 신호 목록 새로고침
            await this.loadSignals();
            
        } catch (error) {
            console.error('실패 신호 정리 오류:', error);
            alert('❌ 실패 신호 정리 중 오류가 발생했습니다.');
        } finally {
            cleanupBtn.disabled = false;
            cleanupBtn.innerHTML = originalText;
        }
    }
}

// 초기화
document.addEventListener('DOMContentLoaded', () => {
    new SignalLifecycleTracker();
});



class ChartManager {
    constructor(app) {
        this.app = app;
        this.currentChart = null;
        this.currentStockCode = null;
        this.currentStockName = null;
        this.currentStrategyType = null;
        
        this.setupEventListeners();
    }

    setupEventListeners() {
        // 차트 모달이 표시될 때 기간 변경 이벤트 리스너 등록
        document.getElementById('chartModal').addEventListener('shown.bs.modal', () => {
            const periodRadios = document.querySelectorAll('input[name="chartPeriod"]');
            periodRadios.forEach(radio => {
                radio.addEventListener('change', () => {
                    if (this.currentStrategyType) {
                        this.loadStrategyChart(this.currentStrategyType);
                    } else {
                        this.loadChart();
                    }
                });
            });
        });
    }
    
    showStockChart(stockCode, stockName) {
        this.currentStockCode = stockCode;
        this.currentStockName = stockName;
        this.currentStrategyType = null;
        
        // 차트 모달 표시
        document.getElementById('chartStockName').textContent = stockName;
        document.getElementById('chartStockCode').textContent = stockCode;
        
        const chartModal = new bootstrap.Modal(document.getElementById('chartModal'));
        chartModal.show();
        
        // 기본 차트 로드
        this.loadChart();
    }

    async showStrategyChart(strategyType) {
        if (!this.currentStockCode) {
            console.error('종목이 선택되지 않았습니다.');
            return;
        }

        this.currentStrategyType = strategyType;
        await this.loadStrategyChart(strategyType);
    }

    async loadChart() {
        const container = document.getElementById('chartContainer');
        const period = document.querySelector('input[name="chartPeriod"]:checked').value;
        
        container.innerHTML = `
            <div class="text-center p-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">차트 로딩 중...</span>
                </div>
                <p class="mt-2 mb-0">차트를 불러오는 중...</p>
            </div>
        `;

        try {
            const response = await fetch(`/chart/image/${this.currentStockCode}?period=${period}`);
            const data = await response.json();

            if (response.ok) {
                container.innerHTML = `<img src="${data.image}" class="img-fluid" alt="차트">`;
            } else {
                container.innerHTML = `
                    <div class="text-center text-danger p-4">
                        <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                        <p>차트를 불러올 수 없습니다.</p>
                    </div>
                `;
            }
        } catch (error) {
            console.error('차트 로드 오류:', error);
            container.innerHTML = `
                <div class="text-center text-danger p-4">
                    <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                    <p>차트 로드 중 오류가 발생했습니다.</p>
                </div>
            `;
        }
    }

    async loadStrategyChart(strategyType) {
        const container = document.getElementById('chartContainer');
        const period = document.querySelector('input[name="chartPeriod"]:checked').value;
        
        container.innerHTML = `
            <div class="text-center p-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">전략 차트 로딩 중...</span>
                </div>
                <p class="mt-2 mb-0">${strategyType} 차트를 불러오는 중...</p>
            </div>
        `;

        try {
            const response = await fetch(`/chart/strategy/${this.currentStockCode}/${strategyType}?period=${period}`);
            const data = await response.json();

            if (response.ok) {
                container.innerHTML = `
                    <div class="mb-2">
                        <span class="badge bg-primary">${data.strategy_type} 전략</span>
                        <span class="badge bg-secondary">${data.period}</span>
                    </div>
                    <img src="${data.image}" class="img-fluid" alt="${strategyType} 차트">
                `;
            } else {
                container.innerHTML = `
                    <div class="text-center text-danger p-4">
                        <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                        <p>${strategyType} 차트를 불러올 수 없습니다.</p>
                    </div>
                `;
            }
        } catch (error) {
            console.error('전략 차트 로드 오류:', error);
            container.innerHTML = `
                <div class="text-center text-danger p-4">
                    <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                    <p>전략 차트 로드 중 오류가 발생했습니다.</p>
                </div>
            `;
        }
    }

}
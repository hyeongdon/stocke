class UIUtils {
    constructor() {
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
    
    updateLastRefreshTime() {
        const timeElement = document.getElementById('lastRefreshTime');
        if (timeElement) {
            timeElement.textContent = new Date().toLocaleTimeString();
        }
    }
}
class UIUtils {
    formatPrice(price) { /* 가격 포맷팅 */ }
    formatChangeRate(rate) { /* 변동률 포맷팅 */ }
    showLoading(message) { /* 로딩 표시 */ }
    hideLoading() { /* 로딩 숨김 */ }
    showError(message) { /* 에러 표시 */ }
}
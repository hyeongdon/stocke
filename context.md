최근에 메소드가 추가되고 삭제가 많이 됫는데 
현재 프로젝트 기준으로 엔드포인트별 프로세스 흐름도 다이어그램으로 그려줘

DB)  
PendingBuySignal
├── id (PK)
├── condition_id
├── stock_code
├── stock_name
├── detected_at
├── status (PENDING/ORDERED/FAILED)
├── reference_candle_high (대량거래용)
├── reference_candle_date (대량거래용)
└── target_price (대량거래용)

AutoTradeCondition
├── id (PK)
├── condition_name (UNIQUE)
├── api_condition_id
├── is_enabled
└── updated_at

AutoTradeSettings
├── id (PK)
├── is_enabled
├── max_invest_amount
├── stop_loss_rate
├── take_profit_rate
└── updated_at
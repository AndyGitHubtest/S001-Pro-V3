"""
币安WebSocket客户端
提供低延迟实时数据接入
"""
import asyncio
import json
import logging
import time
from typing import Callable, Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum
import threading

# 使用websockets库
import websockets

logger = logging.getLogger(__name__)


class WebSocketState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class LatencyStats:
    """延迟统计"""
    count: int = 0
    total_ms: float = 0
    min_ms: float = float('inf')
    max_ms: float = 0
    
    def add(self, latency_ms: float):
        self.count += 1
        self.total_ms += latency_ms
        self.min_ms = min(self.min_ms, latency_ms)
        self.max_ms = max(self.max_ms, latency_ms)
    
    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count > 0 else 0
    
    def report(self) -> Dict:
        return {
            'count': self.count,
            'avg_ms': round(self.avg_ms, 2),
            'min_ms': round(self.min_ms, 2),
            'max_ms': round(self.max_ms, 2),
            'p50': round(self.avg_ms, 2),  # 简化，实际需要保存所有样本
        }


class BinanceWebSocketClient:
    """
    币安WebSocket客户端
    
    特性:
    1. 自动重连
    2. 心跳保活
    3. 延迟统计
    4. 多币种订阅
    """
    
    # 币安WebSocket端点
    WS_URLS = {
        'spot': 'wss://stream.binance.com:9443/ws',
        'futures': 'wss://fstream.binance.com/ws',
        'testnet': 'wss://stream.testnet.binancefuture.com/ws'
    }
    
    def __init__(self, 
                 market_type: str = 'futures',
                 on_kline: Callable = None,
                 on_trade: Callable = None,
                 on_book_ticker: Callable = None,
                 ping_interval: int = 20,
                 reconnect_delay: int = 5):
        """
        Args:
            market_type: 'spot' or 'futures'
            on_kline: K线数据回调
            on_trade: 成交数据回调
            on_book_ticker: 最优挂单回调
            ping_interval: 心跳间隔(秒)
            reconnect_delay: 重连延迟(秒)
        """
        self.ws_url = self.WS_URLS.get(market_type, self.WS_URLS['futures'])
        self.on_kline = on_kline
        self.on_trade = on_trade
        self.on_book_ticker = on_book_ticker
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay
        
        self.state = WebSocketState.DISCONNECTED
        self.ws = None
        self.subscriptions: Set[str] = set()
        self.latency_stats = LatencyStats()
        self.last_ping_time = 0
        self.message_count = 0
        
        # 运行状态
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
    async def connect(self):
        """建立WebSocket连接"""
        if self.state == WebSocketState.CONNECTED:
            return
            
        self.state = WebSocketState.CONNECTING
        logger.info(f"Connecting to {self.ws_url}...")
        
        try:
            self.ws = await websockets.connect(self.ws_url)
            self.state = WebSocketState.CONNECTED
            self._running = True
            self.last_ping_time = time.time()
            logger.info("WebSocket connected")
            
            # 重新订阅
            if self.subscriptions:
                await self._subscribe_all()
            
            # 启动消息处理
            asyncio.create_task(self._heartbeat())
            asyncio.create_task(self._receive_messages())
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.state = WebSocketState.ERROR
            await self._reconnect()
    
    async def _reconnect(self):
        """断线重连"""
        if not self._running:
            return
            
        self.state = WebSocketState.RECONNECTING
        logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
        
        await asyncio.sleep(self.reconnect_delay)
        
        if self._running:
            await self.connect()
    
    async def disconnect(self):
        """断开连接"""
        self._running = False
        self.state = WebSocketState.DISCONNECTED
        
        if self.ws:
            await self.ws.close()
            self.ws = None
        
        logger.info("WebSocket disconnected")
    
    async def subscribe_klines(self, symbols: List[str], interval: str = "1m"):
        """
        订阅K线数据
        
        Args:
            symbols: 币种列表，如 ['BTCUSDT', 'ETHUSDT']
            interval: K线周期，如 '1m', '5m', '15m'
        """
        streams = [f"{s.lower()}@kline_{interval}" for s in symbols]
        self.subscriptions.update(streams)
        
        if self.state == WebSocketState.CONNECTED:
            await self._subscribe_streams(streams)
    
    async def subscribe_trades(self, symbols: List[str]):
        """订阅成交数据"""
        streams = [f"{s.lower()}@trade" for s in symbols]
        self.subscriptions.update(streams)
        
        if self.state == WebSocketState.CONNECTED:
            await self._subscribe_streams(streams)
    
    async def subscribe_book_tickers(self, symbols: List[str]):
        """订阅最优挂单"""
        streams = [f"{s.lower()}@bookTicker" for s in symbols]
        self.subscriptions.update(streams)
        
        if self.state == WebSocketState.CONNECTED:
            await self._subscribe_streams(streams)
    
    async def _subscribe_all(self):
        """重新订阅所有stream"""
        if self.subscriptions:
            await self._subscribe_streams(list(self.subscriptions))
    
    async def _subscribe_streams(self, streams: List[str]):
        """发送订阅请求"""
        if not self.ws or self.state != WebSocketState.CONNECTED:
            return
        
        msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": int(time.time() * 1000)
        }
        
        await self.ws.send(json.dumps(msg))
        logger.info(f"Subscribed to {len(streams)} streams")
    
    async def _heartbeat(self):
        """心跳保活"""
        while self._running and self.state == WebSocketState.CONNECTED:
            try:
                # 检查是否需要发送ping
                if time.time() - self.last_ping_time > self.ping_interval:
                    await self.ws.ping()
                    self.last_ping_time = time.time()
                    logger.debug("Ping sent")
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break
        
        # 心跳异常，触发重连
        if self._running:
            await self._reconnect()
    
    async def _receive_messages(self):
        """接收并处理消息"""
        while self._running and self.state == WebSocketState.CONNECTED:
            try:
                msg = await asyncio.wait_for(
                    self.ws.recv(), 
                    timeout=self.ping_interval + 10
                )
                
                self.message_count += 1
                await self._handle_message(msg)
                
            except asyncio.TimeoutError:
                logger.warning("Receive timeout, reconnecting...")
                await self._reconnect()
                break
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Connection closed, reconnecting...")
                await self._reconnect()
                break
            except Exception as e:
                logger.error(f"Receive error: {e}")
                await self._reconnect()
                break
    
    async def _handle_message(self, msg: str):
        """处理收到的消息"""
        try:
            data = json.loads(msg)
            
            # 计算延迟（如果有服务器时间戳）
            if 'E' in data:  # 事件时间
                server_time = data['E']
                local_time = int(time.time() * 1000)
                latency_ms = local_time - server_time
                self.latency_stats.add(latency_ms)
            
            # 分发到对应的回调
            stream = data.get('stream', '')
            
            if 'kline' in stream and self.on_kline:
                await self._handle_kline(data)
            elif 'trade' in stream and self.on_trade:
                await self._handle_trade(data)
            elif 'bookTicker' in stream and self.on_book_ticker:
                await self._handle_book_ticker(data)
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON: {msg[:100]}")
        except Exception as e:
            logger.error(f"Handle message error: {e}")
    
    async def _handle_kline(self, data: Dict):
        """处理K线数据"""
        kline = data.get('k', {})
        if not kline:
            return
        
        kline_data = {
            'symbol': kline.get('s'),
            'interval': kline.get('i'),
            'open': float(kline.get('o', 0)),
            'high': float(kline.get('h', 0)),
            'low': float(kline.get('l', 0)),
            'close': float(kline.get('c', 0)),
            'volume': float(kline.get('v', 0)),
            'start_time': kline.get('t'),
            'end_time': kline.get('T'),
            'is_closed': kline.get('x', False)
        }
        
        if self.on_kline:
            try:
                if asyncio.iscoroutinefunction(self.on_kline):
                    await self.on_kline(kline_data)
                else:
                    self.on_kline(kline_data)
            except Exception as e:
                logger.error(f"Kline callback error: {e}")
    
    async def _handle_trade(self, data: Dict):
        """处理成交数据"""
        trade_data = {
            'symbol': data.get('s'),
            'price': float(data.get('p', 0)),
            'qty': float(data.get('q', 0)),
            'time': data.get('T'),
            'is_buyer_maker': data.get('m', False)
        }
        
        if self.on_trade:
            try:
                if asyncio.iscoroutinefunction(self.on_trade):
                    await self.on_trade(trade_data)
                else:
                    self.on_trade(trade_data)
            except Exception as e:
                logger.error(f"Trade callback error: {e}")
    
    async def _handle_book_ticker(self, data: Dict):
        """处理最优挂单"""
        ticker_data = {
            'symbol': data.get('s'),
            'bid_price': float(data.get('b', 0)),
            'bid_qty': float(data.get('B', 0)),
            'ask_price': float(data.get('a', 0)),
            'ask_qty': float(data.get('A', 0))
        }
        
        if self.on_book_ticker:
            try:
                if asyncio.iscoroutinefunction(self.on_book_ticker):
                    await self.on_book_ticker(ticker_data)
                else:
                    self.on_book_ticker(ticker_data)
            except Exception as e:
                logger.error(f"Book ticker callback error: {e}")
    
    def get_latency_report(self) -> Dict:
        """获取延迟报告"""
        return {
            'state': self.state.value,
            'message_count': self.message_count,
            'subscriptions': len(self.subscriptions),
            'latency': self.latency_stats.report()
        }
    
    def start(self):
        """启动客户端（同步接口）"""
        if self._thread and self._thread.is_alive():
            return
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.connect())
            loop.run_forever()
        
        self._thread = threading.Thread(target=run_async, daemon=True)
        self._thread.start()
        logger.info("WebSocket client started")
    
    def stop(self):
        """停止客户端（同步接口）"""
        self._running = False
        
        if self._thread:
            # 创建新的事件循环来关闭连接
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.disconnect())
                loop.close()
            except Exception as e:
                logger.error(f"Stop error: {e}")
            
            self._thread.join(timeout=5)
            logger.info("WebSocket client stopped")


class WebSocketManager:
    """
    WebSocket管理器
    管理多个币种的订阅
    """
    
    def __init__(self):
        self.clients: Dict[str, BinanceWebSocketClient] = {}
        self.data_cache: Dict[str, Dict] = {}
        
    def create_client(self, 
                     client_id: str,
                     market_type: str = 'futures',
                     **kwargs) -> BinanceWebSocketClient:
        """创建新的WebSocket客户端"""
        client = BinanceWebSocketClient(
            market_type=market_type,
            **kwargs
        )
        self.clients[client_id] = client
        return client
    
    def get_client(self, client_id: str) -> Optional[BinanceWebSocketClient]:
        """获取客户端"""
        return self.clients.get(client_id)
    
    def stop_all(self):
        """停止所有客户端"""
        for client_id, client in self.clients.items():
            logger.info(f"Stopping client: {client_id}")
            client.stop()
        self.clients.clear()
    
    def get_all_latency_reports(self) -> Dict[str, Dict]:
        """获取所有客户端的延迟报告"""
        return {
            client_id: client.get_latency_report()
            for client_id, client in self.clients.items()
        }


# 使用示例
if __name__ == "__main__":
    import sys
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 回调函数
    def on_kline(data):
        print(f"[KLINE] {data['symbol']} {data['interval']} "
              f"O:{data['open']:.2f} H:{data['high']:.2f} "
              f"L:{data['low']:.2f} C:{data['close']:.2f}")
    
    def on_book_ticker(data):
        print(f"[TICKER] {data['symbol']} "
              f"Bid:{data['bid_price']:.2f} Ask:{data['ask_price']:.2f}")
    
    # 创建客户端
    client = BinanceWebSocketClient(
        market_type='futures',
        on_kline=on_kline,
        on_book_ticker=on_book_ticker
    )
    
    async def main():
        # 连接
        await client.connect()
        
        # 订阅BTC和ETH的1分钟K线
        await client.subscribe_klines(['BTCUSDT', 'ETHUSDT'], '1m')
        
        # 订阅最优挂单
        await client.subscribe_book_tickers(['BTCUSDT', 'ETHUSDT'])
        
        # 运行60秒
        print("\nRunning for 60 seconds...")
        print("Press Ctrl+C to stop\n")
        
        try:
            for i in range(60):
                await asyncio.sleep(1)
                
                # 每10秒打印延迟统计
                if (i + 1) % 10 == 0:
                    report = client.get_latency_report()
                    print(f"\n[Stats] Messages: {report['message_count']}, "
                          f"Avg Latency: {report['latency']['avg_ms']}ms")
                    
        except KeyboardInterrupt:
            pass
        
        # 断开连接
        await client.disconnect()
        
        # 打印最终报告
        final_report = client.get_latency_report()
        print("\n" + "="*60)
        print("Final Report:")
        print("="*60)
        print(f"Total Messages: {final_report['message_count']}")
        print(f"Latency (avg/min/max): "
              f"{final_report['latency']['avg_ms']}ms / "
              f"{final_report['latency']['min_ms']}ms / "
              f"{final_report['latency']['max_ms']}ms")
        print("="*60)
    
    # 运行
    asyncio.run(main())

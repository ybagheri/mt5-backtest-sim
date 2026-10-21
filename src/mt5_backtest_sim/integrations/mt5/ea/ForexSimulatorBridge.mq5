//+------------------------------------------------------------------+
//|                                             ForexSimulatorBridge.mq5
//+------------------------------------------------------------------+
//| The MetaTrader 5 side of the mt5-backtest-sim bridge.
//
//| The simulator owns a Windows named pipe and this expert connects to
//| it. The expert then:
//
//|   * answers the simulator's ping, so it knows the terminal is alive;
//|   * answers ATR requests, because the terminal computes ATR far
//|     faster and more accurately than the simulator could;
//|   * writes the quotes and the bars the simulator produced into the
//|     terminal's common folder, so an indicator or a script running in
//|     the terminal can read the same market the simulator sees.
//
//| The protocol is documented in
//| src/mt5_backtest_sim/integrations/mt5/protocol.py. Every integer is
//| little endian; a string is a four byte byte count followed by
//| UTF-16LE bytes.
//+------------------------------------------------------------------+
#property copyright "mt5-backtest-sim"
#property version   "1.00"
#property strict

//--- Message identifiers. The numbering is part of the protocol.
#define MSG_PING                2
#define MSG_QUIT                3
#define MSG_REFRESH_RATES       4
#define MSG_SYMBOLS             5
#define MSG_UPDATE_CHARTS       8
#define MSG_GET_ATR            13

//--- Bridge input parameters.
input string InpPipeName     = "mt5_backtest_sim"; // Named pipe of the simulator
input int    InpTimerMs      = 100;                 // Poll interval, milliseconds
input int    InpAtrPeriod    = 14;                  // ATR period used for the replies
input int    InpAtrTimeframe = PERIOD_M1;           // Timeframe of the ATR replies

//+------------------------------------------------------------------+
//| Little endian primitives.                                          |
//+------------------------------------------------------------------+
int ReadInt(const uchar &data[], const int offset)
  {
   int value = 0;
   for(int i = 3; i >= 0; i--)
     {
      int part = (int)data[offset + i] & 0xFF;
      value = (value << 8) | part;
     }
   return value;
  }

long ReadLong(const uchar &data[], const int offset)
  {
   long value = 0;
   for(int i = 7; i >= 0; i--)
     {
      long part = (long)((int)data[offset + i] & 0xFF);
      value = (value << 8) | part;
     }
   return value;
  }

double ReadDouble(const uchar &data[], const int offset)
  {
   ulong raw = 0;
   for(int i = 7; i >= 0; i--)
      raw = (raw << 8) | (ulong)((int)data[offset + i] & 0xFF);
   return (double)raw;
  }

//+------------------------------------------------------------------+
//| Read a length prefixed UTF-16 string. Returns the number of bytes |
//| consumed, or -1 when the payload is too short.                    |
//+------------------------------------------------------------------+
int ReadString(const uchar &data[], const int offset, string &text)
  {
   if(ArraySize(data) < offset + 4)
      return -1;
   int length = ReadInt(data, offset);
   if(ArraySize(data) < offset + 4 + length)
      return -1;
   text = "";
   for(int i = 0; i < length; i += 2)
     {
      ushort code = (ushort)(((ushort)data[offset + 4 + i + 1] << 8) |
                             (ushort)data[offset + 4 + i]);
      text += StringFormat("%c", code);
     }
   return 4 + length;
  }

//+------------------------------------------------------------------+
//| Write primitives.                                                  |
//+------------------------------------------------------------------+
void WriteInt(uchar &data[], const int value)
  {
   int size = ArraySize(data);
   ArrayResize(data, size + 4);
   for(int i = 0; i < 4; i++)
      data[size + i] = (uchar)((value >> (8 * i)) & 0xFF);
  }

void WriteDouble(uchar &data[], const double value)
  {
   ulong raw = (ulong)value;
   int size = ArraySize(data);
   ArrayResize(data, size + 8);
   for(int i = 0; i < 8; i++)
      data[size + i] = (uchar)((raw >> (8 * i)) & 0xFF);
  }

//+------------------------------------------------------------------+
//| Bridge state.                                                      |
//+------------------------------------------------------------------+
int    g_file = INVALID_HANDLE;
string g_symbol = "";
int    g_quotes_file = INVALID_HANDLE;
int    g_bars_file = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Open the named pipe.                                               |
//+------------------------------------------------------------------+
bool ConnectPipe()
  {
   string path = "\\\\.\\pipe\\" + InpPipeName;
   g_file = FileOpen(path, FILE_READ | FILE_WRITE | FILE_BIN);
   if(g_file == INVALID_HANDLE)
     {
      PrintFormat("Bridge: cannot open the pipe %s (error %d)", path, GetLastError());
      return false;
     }
   PrintFormat("Bridge: connected to %s", path);
   return true;
  }

//+------------------------------------------------------------------+
//| Send one message.                                                  |
//+------------------------------------------------------------------+
bool SendMessage(const int message_id, const uchar &payload[])
  {
   if(g_file == INVALID_HANDLE)
      return false;
   uchar frame[];
   ArrayResize(frame, 4 + ArraySize(payload));
   WriteInt(frame, message_id);
   for(int i = 0; i < ArraySize(payload); i++)
      frame[4 + i] = payload[i];
   uint written = FileWriteArray(g_file, frame);
   if(written != (uint)ArraySize(frame))
     {
      PrintFormat("Bridge: cannot write to the pipe (error %d)", GetLastError());
      return false;
     }
   FileFlush(g_file);
   return true;
  }

//+------------------------------------------------------------------+
//| Read one message into the buffer. Returns its identifier, or -1    |
//| when there is nothing to read.                                    |
//+------------------------------------------------------------------+
int ReadMessage(uchar &data[])
  {
   ArrayResize(data, 0);
   if(g_file == INVALID_HANDLE)
      return -1;
   if(FileIsEnding(g_file))
     {
      Print("Bridge: the simulator closed the pipe");
      // The handle has to be released, or the pipe stays busy and a restarted
      // simulator cannot open it.
      FileClose(g_file);
      g_file = INVALID_HANDLE;
      return -1;
     }
   uchar chunk[];
   uint read = FileReadArray(g_file, chunk, 65536);
   if(read == 0)
      return -1;
   ArrayResize(data, (int)read);
   for(int i = 0; i < (int)read; i++)
      data[i] = chunk[i];
   if(ArraySize(data) < 4)
      return -1;
   return ReadInt(data, 0);
  }

//+------------------------------------------------------------------+
//| Open the quote file in the common folder.                         |
//+------------------------------------------------------------------+
void OpenQuoteFile()
  {
   if(g_quotes_file != INVALID_HANDLE)
      return;
   g_quotes_file = FileOpen("sim_quotes.csv",
                            FILE_WRITE | FILE_READ | FILE_CSV | FILE_COMMON, ',');
   if(g_quotes_file == INVALID_HANDLE)
      PrintFormat("Bridge: cannot open the quote file (error %d)", GetLastError());
   else
      FileWriteString(g_quotes_file, "time,symbol,bid,ask\n");
  }

//+------------------------------------------------------------------+
//| Open the bar file of one symbol in the common folder.             |
//+------------------------------------------------------------------+
void OpenBarFile(const string &symbol)
  {
   if(g_bars_file != INVALID_HANDLE)
     {
      FileClose(g_bars_file);
      g_bars_file = INVALID_HANDLE;
     }
   g_bars_file = FileOpen(StringFormat("sim_bars_%s.csv", symbol),
                          FILE_WRITE | FILE_READ | FILE_CSV | FILE_COMMON, ',');
   if(g_bars_file == INVALID_HANDLE)
      PrintFormat("Bridge: cannot open the bar file (error %d)", GetLastError());
   else
      FileWriteString(g_bars_file, "time,open,high,low,close,volume\n");
  }

//+------------------------------------------------------------------+
//| Answer an ATR request with the value the terminal computed.       |
//+------------------------------------------------------------------+
void AnswerAtr(const string &symbol, const int timeframe, const int period)
  {
   double atr = 0.0;
   if(period > 0)
      atr = iATR(symbol, (ENUM_TIMEFRAMES)timeframe, period);
   uchar payload[];
   WriteDouble(payload, atr);
   SendMessage(13, payload);
  }

//+------------------------------------------------------------------+
//| Write the quotes the simulator produced.                          |
//+------------------------------------------------------------------+
void ApplyRates(const long time, const uchar &data[], const int offset, const int count)
  {
   int cursor = offset;
   OpenQuoteFile();
   for(int index = 0; index < count; index++)
     {
      string symbol = "";
      int consumed = ReadString(data, cursor, symbol);
      if(consumed < 0)
         return;
      cursor += consumed;
      double bid = ReadDouble(data, cursor);
      cursor += 8;
      double ask = ReadDouble(data, cursor);
      cursor += 8;
      if(g_quotes_file != INVALID_HANDLE)
        {
         FileWriteString(g_quotes_file,
                         StringFormat("%I64d,%s,%.5f,%.5f\n",
                                      time, symbol, bid, ask));
         FileFlush(g_quotes_file);
        }
     }
  }

//+------------------------------------------------------------------+
//| Write the bars the simulator produced.                            |
//+------------------------------------------------------------------+
void ApplyBars(const uchar &data[], const int offset, const int count)
  {
   int cursor = offset;
   for(int index = 0; index < count; index++)
     {
      long time_value = ReadLong(data, cursor);
      cursor += 8;
      double open_value = ReadDouble(data, cursor);
      cursor += 8;
      double high = ReadDouble(data, cursor);
      cursor += 8;
      double low = ReadDouble(data, cursor);
      cursor += 8;
      double close = ReadDouble(data, cursor);
      cursor += 8;
      int volume = ReadInt(data, cursor);
      cursor += 4;
      if(g_bars_file != INVALID_HANDLE)
        {
         FileWriteString(g_bars_file,
                         StringFormat("%I64d,%.5f,%.5f,%.5f,%.5f,%d\n",
                                      time_value, open_value, high, low, close, volume));
         FileFlush(g_bars_file);
        }
     }
  }

//+------------------------------------------------------------------+
//| Handle one message.                                                |
//+------------------------------------------------------------------+
void HandleMessage(const int message_id, const uchar &data[])
  {
   switch(message_id)
     {
      case MSG_PING:
        {
         uchar payload[];
         WriteInt(payload, 1);
         SendMessage(2, payload);
         break;
        }
      case MSG_QUIT:
        {
         Print("Bridge: the simulator asked to close");
         if(g_file != INVALID_HANDLE)
           {
            FileClose(g_file);
            g_file = INVALID_HANDLE;
           }
         break;
        }
      case MSG_GET_ATR:
        {
         int cursor = 4;
         string symbol = "";
         int consumed = ReadString(data, cursor, symbol);
         if(consumed < 0)
            return;
         cursor += consumed;
         int timeframe = ReadInt(data, cursor);
         int period = ReadInt(data, cursor + 4);
         AnswerAtr(symbol, timeframe, period);
         break;
        }
      case MSG_REFRESH_RATES:
        {
         long time_value = ReadLong(data, 4);
         int count = ReadInt(data, 12);
         ApplyRates(time_value, data, 16, count);
         break;
        }
      case MSG_UPDATE_CHARTS:
        {
         int cursor = 4;
         string symbol = "";
         int consumed = ReadString(data, cursor, symbol);
         if(consumed < 0)
            return;
         cursor += consumed;
         int timeframe = ReadInt(data, cursor);
         cursor += 4;
         int count = ReadInt(data, cursor);
         cursor += 4;
         g_symbol = symbol;
         OpenBarFile(symbol);
         ApplyBars(data, cursor, count);
         break;
        }
      default:
         break;
     }
  }

//+------------------------------------------------------------------+
//| Expert initialisation.                                             |
//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(InpTimerMs);
   if(!ConnectPipe())
      return INIT_FAILED;
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialisation.                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(g_file != INVALID_HANDLE)
     {
      FileClose(g_file);
      g_file = INVALID_HANDLE;
     }
   if(g_quotes_file != INVALID_HANDLE)
      FileClose(g_quotes_file);
   if(g_bars_file != INVALID_HANDLE)
      FileClose(g_bars_file);
  }

//+------------------------------------------------------------------+
//| Poll the pipe.                                                     |
//+------------------------------------------------------------------+
void OnTimer()
  {
   if(g_file == INVALID_HANDLE)
     {
      // The simulator went away. Keep trying, so a restarted simulator is
      // picked up without the chart having to be touched again.
      static uint attempts = 0;
      attempts++;
      if(attempts % 10 == 0)
         ConnectPipe();
      return;
     }
   uchar data[];
   int message_id = ReadMessage(data);
   if(message_id >= 0)
      HandleMessage(message_id, data);
  }

Attribute VB_Name = "StockSnapshotExcelModule"
Option Explicit

' 사용법:
' 1) 이 모듈 가져오기 후 "현재가조회_버튼"을 버튼에 연결
' 2) 시트에 다음 헤더 텍스트가 있어야 함:
'    - 응답코드, 응답메시지
'    - 종목코드, 종목이름, 현재가, 전일대비, 등락률, 거래량
'    - 시간, 매도잔량, 호가, 매수잔량

Private Const API_BASE_URL As String = "http://144.24.81.83:8001"
Private Const ORDERBOOK_ROWS As Long = 10

Public Sub 현재가조회_버튼()
    Dim ws As Worksheet
    Dim codeCell As Range
    Dim stockCode As String
    Dim url As String
    Dim http As Object
    Dim body As String
    Dim ok As Boolean
    Dim message As String
    Dim i As Long
    Dim orderRows As Collection
    Dim rowText As Variant
    Dim writeRow As Long
    Dim timeHeader As Range
    Dim t As String, askQty As String, askPx As String, bidQty As String

    Set ws = ActiveSheet
    Set codeCell = FindHeaderValueCell(ws, "종목코드")
    If codeCell Is Nothing Then
        MsgBox "'종목코드' 헤더를 찾을 수 없습니다.", vbExclamation
        Exit Sub
    End If

    stockCode = NormalizeStockCode(CStr(codeCell.Value))
    If Len(stockCode) = 0 Then
        MsgBox "종목코드를 입력해 주세요.", vbExclamation
        Exit Sub
    End If

    url = API_BASE_URL & "/stocks/" & stockCode & "/snapshot"

    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.Open "GET", url, False
    http.SetRequestHeader "Accept", "application/json"
    http.Send

    body = CStr(http.ResponseText)
    ok = CBoolSafe(GetJsonPrimitive(body, "success"))

    If http.Status <> 200 Or Not ok Then
        message = GetJsonPrimitive(body, "message")
        If Len(message) = 0 Then message = "스냅샷 조회 실패"
        WriteByHeader ws, "응답코드", CStr(http.Status)
        WriteByHeader ws, "응답메시지", message
        MsgBox "조회 실패: " & message, vbExclamation
        Exit Sub
    End If

    WriteByHeader ws, "응답코드", "200"
    WriteByHeader ws, "응답메시지", "정상"

    WriteByHeader ws, "종목코드", stockCode
    WriteByHeader ws, "종목이름", GetJsonPrimitive(body, "stock_name")
    WriteByHeader ws, "현재가", ToNumber(GetJsonPrimitive(body, "current_price"))
    WriteByHeader ws, "전일대비", ToNumber(GetJsonPrimitive(body, "price_diff"))
    WriteByHeader ws, "등락률", GetJsonPrimitive(body, "change_rate")
    WriteByHeader ws, "거래량", ToNumber(GetJsonPrimitive(body, "volume"))

    Set orderRows = ExtractOrderbookRows(body)
    ClearOrderbookRows ws

    Set timeHeader = FindHeaderCell(ws, "시간")
    If timeHeader Is Nothing Then
        MsgBox "'시간' 헤더를 찾을 수 없습니다.", vbExclamation
        Exit Sub
    End If
    writeRow = timeHeader.Row

    t = GetJsonPrimitive(body, "orderbook_time")
    If Len(t) = 0 Then t = Format$(Now, "hh:nn:ss")

    For i = 1 To WorksheetFunction.Min(orderRows.Count, ORDERBOOK_ROWS)
        rowText = orderRows(i)
        askQty = GetJsonPrimitive(CStr(rowText), "ask_qty")
        askPx = GetJsonPrimitive(CStr(rowText), "ask_price")
        bidQty = GetJsonPrimitive(CStr(rowText), "bid_qty")

        ws.Cells(writeRow + i, FindHeaderCell(ws, "시간").Column).Value = t
        ws.Cells(writeRow + i, FindHeaderCell(ws, "매도잔량").Column).Value = ToNumber(askQty)
        ws.Cells(writeRow + i, FindHeaderCell(ws, "호가").Column).Value = ToNumber(askPx)
        ws.Cells(writeRow + i, FindHeaderCell(ws, "매수잔량").Column).Value = ToNumber(bidQty)
    Next i

    MsgBox "현재가/호가 조회 완료", vbInformation
End Sub

Private Sub ClearOrderbookRows(ByVal ws As Worksheet)
    Dim i As Long
    Dim baseRow As Long
    Dim cTime As Long, cAsk As Long, cPx As Long, cBid As Long
    Dim hTime As Range, hAsk As Range, hPx As Range, hBid As Range

    Set hTime = FindHeaderCell(ws, "시간")
    Set hAsk = FindHeaderCell(ws, "매도잔량")
    Set hPx = FindHeaderCell(ws, "호가")
    Set hBid = FindHeaderCell(ws, "매수잔량")
    If hTime Is Nothing Or hAsk Is Nothing Or hPx Is Nothing Or hBid Is Nothing Then Exit Sub

    baseRow = hTime.Row
    cTime = hTime.Column
    cAsk = hAsk.Column
    cPx = hPx.Column
    cBid = hBid.Column

    For i = 1 To ORDERBOOK_ROWS
        ws.Cells(baseRow + i, cTime).ClearContents
        ws.Cells(baseRow + i, cAsk).ClearContents
        ws.Cells(baseRow + i, cPx).ClearContents
        ws.Cells(baseRow + i, cBid).ClearContents
    Next i
End Sub

Private Function ExtractOrderbookRows(ByVal jsonText As String) As Collection
    Dim regexBlock As Object
    Dim blockMatches As Object
    Dim regexObj As Object
    Dim objMatches As Object
    Dim m As Object
    Dim rows As Collection

    Set rows = New Collection
    Set regexBlock = CreateObject("VBScript.RegExp")
    regexBlock.Global = False
    regexBlock.MultiLine = True
    regexBlock.Pattern = """" & "orderbook" & """" & "\s*:\s*\[([\s\S]*?)\]"

    Set blockMatches = regexBlock.Execute(jsonText)
    If blockMatches.Count = 0 Then
        Set ExtractOrderbookRows = rows
        Exit Function
    End If

    Set regexObj = CreateObject("VBScript.RegExp")
    regexObj.Global = True
    regexObj.MultiLine = True
    regexObj.Pattern = "\{[^{}]*\}"

    Set objMatches = regexObj.Execute(CStr(blockMatches(0).SubMatches(0)))
    For Each m In objMatches
        rows.Add CStr(m.Value)
    Next m

    Set ExtractOrderbookRows = rows
End Function

Private Function FindHeaderCell(ByVal ws As Worksheet, ByVal headerText As String) As Range
    Dim found As Range
    Set found = ws.Cells.Find(What:=headerText, LookIn:=xlValues, LookAt:=xlWhole, MatchCase:=False)
    If Not found Is Nothing Then
        Set FindHeaderCell = found
    End If
End Function

Private Function FindHeaderValueCell(ByVal ws As Worksheet, ByVal headerText As String) As Range
    Dim h As Range
    Set h = FindHeaderCell(ws, headerText)
    If h Is Nothing Then Exit Function
    Set FindHeaderValueCell = ws.Cells(h.Row + 1, h.Column)
End Function

Private Sub WriteByHeader(ByVal ws As Worksheet, ByVal headerText As String, ByVal valueText As Variant)
    Dim vCell As Range
    Set vCell = FindHeaderValueCell(ws, headerText)
    If vCell Is Nothing Then Exit Sub
    vCell.Value = valueText
End Sub

Private Function GetJsonPrimitive(ByVal jsonText As String, ByVal key As String) As String
    Dim regex As Object
    Dim matches As Object
    Dim rawValue As String
    Set regex = CreateObject("VBScript.RegExp")
    regex.Global = False
    regex.MultiLine = True
    regex.Pattern = """" & EscapeRegex(key) & """" & "\s*:\s*(""(?:[^""\\]|\\.)*""|true|false|null|-?\d+(?:\.\d+)?)"

    Set matches = regex.Execute(jsonText)
    If matches.Count = 0 Then
        GetJsonPrimitive = ""
        Exit Function
    End If

    rawValue = CStr(matches(0).SubMatches(0))
    GetJsonPrimitive = DecodeJsonPrimitive(rawValue)
End Function

Private Function DecodeJsonPrimitive(ByVal rawValue As String) As String
    Dim v As String
    v = Trim$(rawValue)
    If LCase$(v) = "null" Then
        DecodeJsonPrimitive = ""
        Exit Function
    End If

    If Len(v) >= 2 And Left$(v, 1) = """" And Right$(v, 1) = """" Then
        v = Mid$(v, 2, Len(v) - 2)
        v = Replace(v, "\" & Chr$(34), Chr$(34))
        v = Replace(v, "\\", "\")
        v = Replace(v, "\/", "/")
        v = Replace(v, "\n", vbLf)
        v = Replace(v, "\r", vbCr)
        v = Replace(v, "\t", vbTab)
    End If

    DecodeJsonPrimitive = v
End Function

Private Function EscapeRegex(ByVal text As String) As String
    Dim t As String
    t = text
    t = Replace(t, "\", "\\")
    t = Replace(t, ".", "\.")
    t = Replace(t, "+", "\+")
    t = Replace(t, "*", "\*")
    t = Replace(t, "?", "\?")
    t = Replace(t, "^", "\^")
    t = Replace(t, "$", "\$")
    t = Replace(t, "(", "\(")
    t = Replace(t, ")", "\)")
    t = Replace(t, "[", "\[")
    t = Replace(t, "]", "\]")
    t = Replace(t, "{", "\{")
    t = Replace(t, "}", "\}")
    t = Replace(t, "|", "\|")
    EscapeRegex = t
End Function

Private Function ToNumber(ByVal rawValue As Variant) As Double
    Dim txt As String
    txt = Trim$(CStr(rawValue))
    txt = Replace(txt, ",", "")
    txt = Replace(txt, "%", "")
    txt = Replace(txt, "+", "")
    If Len(txt) = 0 Then
        ToNumber = 0
    ElseIf IsNumeric(txt) Then
        ToNumber = CDbl(txt)
    Else
        ToNumber = 0
    End If
End Function

Private Function CBoolSafe(ByVal value As Variant) As Boolean
    Dim txt As String
    txt = LCase$(Trim$(CStr(value)))
    CBoolSafe = (txt = "true" Or txt = "1" Or txt = "yes" Or txt = "y")
End Function

Private Function NormalizeStockCode(ByVal codeValue As String) As String
    Dim txt As String
    txt = Trim$(codeValue)
    txt = Replace(txt, "A", "")
    txt = Replace(txt, "-", "")
    txt = Replace(txt, " ", "")
    NormalizeStockCode = txt
End Function

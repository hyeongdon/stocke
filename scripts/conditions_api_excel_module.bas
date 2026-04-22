Attribute VB_Name = "ConditionsApiExcelModule"
Option Explicit

' 필요사항:
' - 외부 모듈/참조 없이 바로 실행 가능 (Late Binding 사용)
'
' API 참고:
' http://144.24.81.83:8001/conditions/

Private Const CONDITIONS_API_URL As String = "http://144.24.81.83:8001/conditions/"
Private Const CONDITIONS_SHEET As String = "Conditions"
Private Const ORDERS_SHEET As String = "AutoOrders"

Public Sub 조건식API_엑셀반영()
    Dim ws As Worksheet
    Dim http As Object
    Dim body As String
    Dim objects As Collection
    Dim objText As Variant
    Dim rowIdx As Long

    Set ws = GetOrCreateSheet(CONDITIONS_SHEET)
    ws.Cells.Clear

    ws.Range("A1").Value = "id"
    ws.Range("B1").Value = "api_id"
    ws.Range("C1").Value = "condition_name"
    ws.Range("D1").Value = "condition_expression"
    ws.Range("E1").Value = "is_active"
    ws.Range("F1").Value = "is_enabled"
    ws.Range("G1").Value = "created_at"
    ws.Range("H1").Value = "updated_at"

    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.Open "GET", CONDITIONS_API_URL, False
    http.SetRequestHeader "Accept", "application/json"
    http.Send

    If http.Status <> 200 Then
        MsgBox "API 호출 실패: HTTP " & http.Status, vbExclamation
        Exit Sub
    End If

    body = CStr(http.ResponseText)
    Set objects = ExtractJsonObjects(body)

    rowIdx = 2
    For Each objText In objects
        ws.Cells(rowIdx, "A").Value = GetJsonField(objText, "id")
        ws.Cells(rowIdx, "B").Value = GetJsonField(objText, "api_id")
        ws.Cells(rowIdx, "C").Value = GetJsonField(objText, "condition_name")
        ws.Cells(rowIdx, "D").Value = GetJsonField(objText, "condition_expression")
        ws.Cells(rowIdx, "E").Value = CBoolSafe(GetJsonField(objText, "is_active"))
        ws.Cells(rowIdx, "F").Value = CBoolSafe(GetJsonField(objText, "is_enabled"))
        ws.Cells(rowIdx, "G").Value = GetJsonField(objText, "created_at")
        ws.Cells(rowIdx, "H").Value = GetJsonField(objText, "updated_at")
        rowIdx = rowIdx + 1
    Next objText

    ws.Columns("A:H").EntireColumn.AutoFit
    MsgBox "조건식 " & (rowIdx - 2) & "건을 가져왔습니다.", vbInformation
End Sub

Public Sub 자동주문_시트초기화()
    Dim ws As Worksheet
    Set ws = GetOrCreateSheet(ORDERS_SHEET)
    ws.Cells.Clear

    ws.Range("A1").Value = "종목코드"
    ws.Range("B1").Value = "종목명"
    ws.Range("C1").Value = "매수가"
    ws.Range("D1").Value = "청산가"
    ws.Range("E1").Value = "수량"
    ws.Range("F1").Value = "주문유형(BUY/SELL)"
    ws.Range("G1").Value = "활성여부(TRUE/FALSE)"
    ws.Range("H1").Value = "검증결과"

    ws.Columns("A:H").EntireColumn.AutoFit
    MsgBox "AutoOrders 시트를 초기화했습니다.", vbInformation
End Sub

Public Sub 자동주문_입력검증()
    Dim ws As Worksheet
    Dim lastRow As Long
    Dim r As Long
    Dim symbolCode As String
    Dim buyPrice As Double
    Dim exitPrice As Double
    Dim qty As Double
    Dim orderType As String
    Dim enabled As Boolean

    Set ws = GetOrCreateSheet(ORDERS_SHEET)
    lastRow = ws.Cells(ws.Rows.Count, "A").End(xlUp).Row

    If lastRow < 2 Then
        MsgBox "검증할 주문 데이터가 없습니다.", vbExclamation
        Exit Sub
    End If

    For r = 2 To lastRow
        symbolCode = Trim$(CStr(ws.Cells(r, "A").Value))
        buyPrice = ToNumber(ws.Cells(r, "C").Value)
        exitPrice = ToNumber(ws.Cells(r, "D").Value)
        qty = ToNumber(ws.Cells(r, "E").Value)
        orderType = UCase$(Trim$(CStr(ws.Cells(r, "F").Value)))
        enabled = CBoolSafe(ws.Cells(r, "G").Value)

        ws.Cells(r, "H").Value = ValidateOrderRow(symbolCode, buyPrice, exitPrice, qty, orderType, enabled)
    Next r

    MsgBox "주문 검증이 완료되었습니다.", vbInformation
End Sub

Private Function ValidateOrderRow(ByVal symbolCode As String, ByVal buyPrice As Double, ByVal exitPrice As Double, ByVal qty As Double, ByVal orderType As String, ByVal enabled As Boolean) As String
    If Not enabled Then
        ValidateOrderRow = "비활성"
        Exit Function
    End If

    If Len(symbolCode) = 0 Then
        ValidateOrderRow = "오류: 종목코드 필요"
        Exit Function
    End If

    If buyPrice <= 0 Or exitPrice <= 0 Then
        ValidateOrderRow = "오류: 매수가/청산가 확인"
        Exit Function
    End If

    If qty <= 0 Then
        ValidateOrderRow = "오류: 수량 확인"
        Exit Function
    End If

    If orderType <> "BUY" And orderType <> "SELL" Then
        ValidateOrderRow = "오류: 주문유형(BUY/SELL)"
        Exit Function
    End If

    ValidateOrderRow = "주문가능"
End Function

Private Function GetOrCreateSheet(ByVal sheetName As String) As Worksheet
    On Error Resume Next
    Set GetOrCreateSheet = ThisWorkbook.Worksheets(sheetName)
    On Error GoTo 0

    If GetOrCreateSheet Is Nothing Then
        Set GetOrCreateSheet = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
        GetOrCreateSheet.Name = sheetName
    End If
End Function

Private Function CBoolSafe(ByVal value As Variant) As Boolean
    Dim txt As String
    txt = LCase$(Trim$(CStr(value)))
    CBoolSafe = (txt = "true" Or txt = "1" Or txt = "yes" Or txt = "y")
End Function

Private Function ExtractJsonObjects(ByVal jsonText As String) As Collection
    Dim regex As Object
    Dim matches As Object
    Dim m As Object
    Dim items As Collection

    Set items = New Collection
    Set regex = CreateObject("VBScript.RegExp")
    regex.Global = True
    regex.MultiLine = True
    regex.Pattern = "\{[^{}]*\}"

    Set matches = regex.Execute(jsonText)
    For Each m In matches
        items.Add CStr(m.Value)
    Next m

    Set ExtractJsonObjects = items
End Function

Private Function GetJsonField(ByVal objectText As String, ByVal key As String) As String
    Dim regex As Object
    Dim matches As Object
    Dim rawValue As String

    Set regex = CreateObject("VBScript.RegExp")
    regex.Global = False
    regex.MultiLine = False
    regex.Pattern = """" & EscapeRegex(key) & """" & "\s*:\s*(""(?:[^""\\]|\\.)*""|true|false|null|-?\d+(?:\.\d+)?)"

    Set matches = regex.Execute(objectText)
    If matches.Count = 0 Then
        GetJsonField = ""
        Exit Function
    End If

    rawValue = CStr(matches(0).SubMatches(0))
    GetJsonField = DecodeJsonPrimitive(rawValue)
End Function

Private Function DecodeJsonPrimitive(ByVal rawValue As String) As String
    Dim v As String
    v = Trim$(rawValue)

    If LCase$(v) = "null" Then
        DecodeJsonPrimitive = ""
        Exit Function
    End If

    If Len(v) >= 2 Then
        If Left$(v, 1) = """" And Right$(v, 1) = """" Then
            v = Mid$(v, 2, Len(v) - 2)
            v = Replace(v, "\" & Chr$(34), Chr$(34))
            v = Replace(v, "\\", "\")
            v = Replace(v, "\/", "/")
            v = Replace(v, "\n", vbLf)
            v = Replace(v, "\r", vbCr)
            v = Replace(v, "\t", vbTab)
        End If
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
    txt = Replace(txt, "원", "")
    txt = Replace(txt, "%", "")

    If Len(txt) = 0 Then
        ToNumber = 0
    ElseIf IsNumeric(txt) Then
        ToNumber = CDbl(txt)
    Else
        ToNumber = 0
    End If
End Function

Attribute VB_Name = "StockConditionModule"
Option Explicit

' 사용 전제:
' - A:F 열 헤더: 종목명, 자산총계, 부채총계, PER, ROE, PBR
' - 1행은 헤더, 2행부터 데이터

Public Sub 조건식출력_프로젝트용()
    Dim ws As Worksheet
    Dim lastRow As Long
    Dim rowIdx As Long
    Dim assetValue As Double
    Dim debtValue As Double
    Dim perValue As Double
    Dim roeValue As Double
    Dim pbrValue As Double
    Dim debtRatio As Double
    Dim resultText As String

    Set ws = ActiveSheet
    lastRow = ws.Cells(ws.Rows.Count, "A").End(xlUp).Row

    ws.Range("G1").Value = "조건식결과"
    ws.Range("H1").Value = "부채비율(%)"

    For rowIdx = 2 To lastRow
        assetValue = ToNumber(ws.Cells(rowIdx, "B").Value)
        debtValue = ToNumber(ws.Cells(rowIdx, "C").Value)
        perValue = ToNumber(ws.Cells(rowIdx, "D").Value)
        roeValue = ToNumber(ws.Cells(rowIdx, "E").Value)
        pbrValue = ToNumber(ws.Cells(rowIdx, "F").Value)

        If assetValue <= 0 Then
            debtRatio = 0
        Else
            debtRatio = (debtValue / assetValue) * 100#
        End If

        ws.Cells(rowIdx, "H").Value = Round(debtRatio, 2)

        resultText = BuildConditionResult(perValue, roeValue, pbrValue, debtRatio)
        ws.Cells(rowIdx, "G").Value = resultText
    Next rowIdx

    MsgBox "조건식 출력 완료: " & (lastRow - 1) & "건", vbInformation
End Sub

Private Function BuildConditionResult(ByVal perValue As Double, ByVal roeValue As Double, ByVal pbrValue As Double, ByVal debtRatio As Double) As String
    If perValue <= 0 Or roeValue = 0 Or pbrValue <= 0 Then
        BuildConditionResult = "데이터확인"
        Exit Function
    End If

    If roeValue >= 12 And perValue <= 15 And pbrValue <= 1.5 And debtRatio <= 100 Then
        BuildConditionResult = "강매수후보"
    ElseIf roeValue >= 8 And perValue <= 20 And pbrValue <= 2.5 And debtRatio <= 150 Then
        BuildConditionResult = "관심종목"
    ElseIf debtRatio > 200 Or roeValue < 0 Then
        BuildConditionResult = "리스크높음"
    Else
        BuildConditionResult = "보통"
    End If
End Function

Private Function ToNumber(ByVal rawValue As Variant) As Double
    Dim txt As String

    If IsError(rawValue) Then
        ToNumber = 0
        Exit Function
    End If

    txt = Trim$(CStr(rawValue))
    If Len(txt) = 0 Then
        ToNumber = 0
        Exit Function
    End If

    txt = Replace(txt, ",", "")
    txt = Replace(txt, "%", "")
    txt = Replace(txt, "N/A", "", 1, -1, vbTextCompare)
    txt = Replace(txt, "-", "")

    If Len(Trim$(txt)) = 0 Then
        ToNumber = 0
    ElseIf IsNumeric(txt) Then
        ToNumber = CDbl(txt)
    Else
        ToNumber = 0
    End If
End Function

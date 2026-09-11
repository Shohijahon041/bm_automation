' run_hidden.vbs - berilgan .bat/.cmd faylni oyna ko'rinmas holatda ishga tushiradi.
' Ishlatilishi: wscript.exe run_hidden.vbs "C:\...\fayl.bat"
Set sh = CreateObject("WScript.Shell")
If WScript.Arguments.Count = 0 Then WScript.Quit 1
sh.Run """" & WScript.Arguments(0) & """", 0, True

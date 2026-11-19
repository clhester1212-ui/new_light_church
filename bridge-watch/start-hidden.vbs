' Starts the Bridge watch helper with no window. Used by the "New Light Bridge Watch" scheduled task.
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Run """C:\Program Files\Companion\resources\node-runtimes\node26\node.exe"" """ & here & "\bridge_watch.js""", 0, False

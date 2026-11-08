# Show the app as LocalVault AI

Your private, local AI assistant.

People see **LocalVault AI**. That means the model runs on this PC. Chat files stay ordinary JSON in `%LOCALAPPDATA%\LocalChat\data`. This app does not encrypt those files, and an iPhone on the same Wi-Fi still uses plain HTTP. The code runner is still not a sandbox. Those limits are unchanged.

The program file, data folder, port, and firewall rule stay where they already are.

| What you see | What stays put |
| --- | --- |
| Window title, browser tab, iPhone Home Screen name | `LocalVault AI` |
| Subtitle | `Your private, local AI assistant.` |
| Program file | `Local Chat.exe` |
| Installed folder | `%LOCALAPPDATA%\Programs\LocalChat\` |
| Chats and settings | `%LOCALAPPDATA%\LocalChat\data` |
| Port | `8080` unless the iPhone panel shows another one |
| Firewall rule name | `Local Chat iPhone LAN` |

Do not install a second copy under a new folder. Replacing `Local Chat.exe` in the folder above keeps shortcut targets valid.

## Build on the MSI

Close LocalVault AI first. The process is still named `Local Chat.exe`. From the updated source folder:

```bat
build_exe.bat
```

That writes `dist\Local Chat\Local Chat.exe`. The window title and the Windows file properties (Product name, File description) say LocalVault AI. The file name does not change.

## Replace the current install

The script copies `%LOCALAPPDATA%\LocalChat\data` to `%LOCALAPPDATA%\LocalChat\backups\<timestamp>\data` and does not delete the original chats. It will not force-quit the app.

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Install-LocalChatUpdate.ps1 -DryRun -InstallDir "$env:LOCALAPPDATA\Programs\LocalChat" -DistDir ".\dist\Local Chat"
powershell -ExecutionPolicy Bypass -File .\deploy\Install-LocalChatUpdate.ps1 -InstallDir "$env:LOCALAPPDATA\Programs\LocalChat" -DistDir ".\dist\Local Chat"
```

`-InstallDir` is the folder that already contains `Local Chat.exe`. Discovery also looks in the older `Programs\Local Chat` and `%USERPROFILE%\local-chat-app` folders. If more than one folder has the program, the script stops and asks you to pass `-InstallDir`.

## Shortcuts

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Rename-LocalVaultShortcuts.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\deploy\Rename-LocalVaultShortcuts.ps1
```

It looks on the Desktop, in the Start menu, and in the taskbar pin folder. A shortcut is renamed from `Local Chat.lnk` to `LocalVault AI.lnk` only when its target file is already `Local Chat.exe`. The target, arguments, working directory, icon, and hotkey are written back unchanged. It does not change the icon.

If `LocalVault AI.lnk` is already in that folder and points at the same exe, only the old `Local Chat.lnk` is removed, so you do not get two icons. If `LocalVault AI.lnk` points somewhere else, both are left alone. The script does not create a shortcut you did not already have, and it does not rename the exe.

A taskbar pin that Windows will not rename in place can be unpinned and pinned again from the same `Local Chat.exe`.

## Taskbar icon

The native window and `Local Chat.exe` use `desktop_assets\icon.ico`: a light chip on a black tile, the same black as the window. It is not a lock. Chats are still ordinary JSON. `build_exe.bat` also copies that file next to `Local Chat.exe` as `icon.ico`, which is the file a pinned shortcut should use.

pywebview on Windows paints the Python icon unless the form is given this file before the window is shown. The program does that, and it sets the taskbar id `LocalChat.Desktop` before any window opens. An old pin does not pick that up by itself.

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Refresh-LocalVaultTaskbarIcon.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\deploy\Refresh-LocalVaultTaskbarIcon.ps1
```

The script only changes shortcuts whose target file is already `Local Chat.exe`. It writes the same target and arguments back, sets the icon to `icon.ico` (or `desktop_assets\icon.ico`, or `_internal\desktop_assets\icon.ico`) next to that program, and sets the shortcut's taskbar id to `LocalChat.Desktop`. A shortcut that points at `python.exe` is left alone. It does not delete shortcuts, it does not change `%LOCALAPPDATA%\LocalChat`, and it does not restart Explorer.

Re-run the helper in a new PowerShell process after replacing this script. The installed `Local Chat.exe` does not need to be replaced for the shortcut id. The property is written with the shortcut opened read-write. A read-only open leaves Desktop and Start menu ids empty and returns `STG_E_ACCESSDENIED`.

Windows can keep the old picture on a pin. If it still looks wrong, unpin that pin and pin `Local Chat.exe` again. The Edge fallback window, used only when the native window cannot start, keeps Edge's own icon.

## Firewall

Leave the existing rule name. `Enable-LocalChatLanFirewall.ps1` still uses the display name `Local Chat iPhone LAN`, Private profile, local subnet, and `Local Chat.exe`. Running it again replaces that same rule instead of adding a second one. If it asks for the program path:

```text
%LOCALAPPDATA%\Programs\LocalChat\Local Chat.exe
```

`Disable-LocalChatLanFirewall.ps1` removes only that rule.

iPhone steps are in `docs/IPHONE.md`. The address is still the one in the iPhone panel, usually `http://<this PC's Wi-Fi address>:8080/`.

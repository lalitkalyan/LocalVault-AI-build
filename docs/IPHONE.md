# Use LocalVault AI from an iPhone

LocalVault AI stays on the Windows PC. The iPhone is only a window onto it. Nothing in this setup publishes the app on the internet, opens a tunnel, or moves Ollama off the PC. "Private" here means the model runs on that PC. Chats are ordinary files, and this connection is plain HTTP.

These steps were checked as source tests on Linux, not on a physical iPhone. The program file stays `Local Chat.exe`, usually at `%LOCALAPPDATA%\Programs\LocalChat\Local Chat.exe`. Example addresses below, such as `http://192.168.1.23:8080/`, are samples. Use the address shown in the app.

## What you are turning on

By default the app listens only on `127.0.0.1`. That is still the case until you opt in.

Opting in listens on the PC's private Wi-Fi address (for example a `192.168.x.x` address) and asks for a one-time pairing code before a phone can open chats, use a model, search, or run Python. The code is shown on the PC. It is not part of the address, and it is not written to the app log.

Ollama keeps using `127.0.0.1:11434`. Do not set `OLLAMA_HOST=0.0.0.0`. Do not forward port 11434 on the router. Do not forward the LocalVault AI port either.

## Before you start

1. Put the MSI and the iPhone on the **same trusted Wi-Fi**. Not mobile data, not a guest network, not a cafe or hotel network.
2. Turn VPN off on both, including Tailscale or any "tunnel" app. Those send the phone's traffic away from the PC.
3. Leave the MSI awake and plugged in if you want a long session. Sleep or closing LocalVault AI drops the phone.
4. The Windows network profile for that Wi-Fi must be **Private**. The firewall script will not switch this for you.
   - Settings → Network & internet → Wi-Fi → the network you are on → Network profile → **Private**.

## On the MSI

1. Close LocalVault AI if it is already open. The program file is still `Local Chat.exe`.
2. Start it in iPhone mode:
   - Double-click `Start-iPhone-Access.bat`, or
   - Start the built app with `Local Chat.exe --lan`, or
   - From source: `python desktop.py --lan`
3. In the LocalVault AI window, open **iPhone** in the sidebar.
4. Read the warning. A paired phone can read and write chats, use models, search the web, and run Python **on the PC**.
5. If you started with `--lan`, access is already on. If you started the normal app, check the box and choose **Allow iPhone on this Wi-Fi**.
6. The panel shows the exact address. It looks like `http://192.168.1.23:8080/` but the numbers will be yours. **Use the address on the screen**, including `http://` and the port. If several addresses are listed, use the one marked "Use this address" (the Wi-Fi the PC is actually using).
7. The panel also shows a pairing code like `AB2K-7QPM`. Leave that window open. The code works once and lasts about 10 minutes.
8. If the iPhone cannot connect at all, Windows is probably blocking the port. In an **Administrator** PowerShell, from the app folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Enable-LocalChatLanFirewall.ps1
```

If the app picked a port other than 8080, pass that port: `-Port 8080` replaced with the port in the address. If the script finds more than one `Local Chat.exe`, it stops and asks for `-Program` with the full path of the one you start. It will not add a rule that applies to every program.

You can preview the rule without changing anything:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Enable-LocalChatLanFirewall.ps1 -DryRun
```

The rule is inbound TCP, **Private** profile only, remote address **LocalSubnet**, and only that program and port. It does not open Ollama. It does not change Public/Private.

## On the iPhone

1. Connect to the same Wi-Fi as the MSI. Confirm Wi-Fi is on and cellular is not being used for this (Safari will use Wi-Fi for a `192.168...` or `10...` address).
2. Open **Safari**. Do not use a search box. Tap the address bar.
3. Type the address from the PC exactly, for example `http://192.168.1.23:8080/`. Use the numbers from the PC, not this example.
   - Do not use `https://`.
   - Do not use a `.local` name.
   - Do not add the pairing code to the address.
4. You should see **Pair this iPhone**. Type the code from the PC and tap **Connect**.
5. The chat screen opens. Sidebar, model list, Web / Reasoning / Code, and the attach button are the same app. The composer sits above the keyboard and the home indicator.
6. Optional Home Screen icon: in Safari tap **Share** → **Add to Home Screen** → **Add**. Open the icon. If it asks for a code again, that icon is a separate window from the Safari tab. On the PC choose **New code** and enter the new one.

A check you can do on the PC before involving the phone: open the same `http://192.168...` address in a browser on the MSI. You should get the pairing page. `http://127.0.0.1:8080/` on the MSI still opens the normal app with no code. If the LAN address does not load on the PC itself, iPhone mode is not listening. Read the error in the iPhone panel.

## Stop or revoke

- **One phone:** iPhone panel → **Revoke** next to that phone. Or on the phone, **Disconnect**.
- **Stop listening:** iPhone panel → **Stop iPhone access**. Paired phones are dropped and the PC stops accepting Wi-Fi connections. `127.0.0.1` keeps working.
- **Close the LocalVault AI window.** That stops the server this window owns, revokes phones, and stops Ollama only if this app started Ollama. It does not leave a background copy listening. The next launch is loopback-only again unless you start with `--lan` or turn the panel on.
- **Firewall rule:** stopping the app does not delete a rule you added. When you no longer want the port allowed:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Disable-LocalChatLanFirewall.ps1
```

That removes only the rule named `Local Chat iPhone LAN`.

## If the iPhone cannot connect

Work down this list. The app cannot punch through a router that isolates clients.

| What you see | What to do |
| --- | --- |
| Safari cannot open the page, and the LAN address also fails on the PC | iPhone mode is off, or the address/port is wrong. Use the panel's current address. |
| The page loads on the PC but not on the phone | Same Wi-Fi name on both. VPN off on both. Not a guest network. Some routers have "AP isolation", "client isolation", or "guest Wi-Fi" that blocks devices from seeing each other. Turn that off for this network, or use a normal private LAN. |
| Windows network is Public | The firewall rule does not apply, on purpose. Switch the PC to Private yourself, then run the enable script again. Do not expect the script to switch it. |
| You never ran the firewall script | Run `Enable-LocalChatLanFirewall.ps1` as Administrator. |
| Code rejected | Codes are one-time and last 10 minutes. Choose **New code** on the PC. Five wrong tries burn the current code. Too many tries from the phone waits about 15 minutes. |
| It worked, then the phone asked to pair again | The PC was closed or restarted, you pressed Stop or Revoke, or the phone's Wi-Fi address changed. Pair again. Closing the app forgets phones on purpose. |
| Home Screen icon asks to pair after Safari already paired | Add to Home Screen can be a separate cookie jar. Enter a new code from the PC. |
| Wrong site or certificate warning | You used `https://` or a name instead of the `http://` address on the panel. Go back and type that address. |
| Chat works on the PC but the phone says Ollama is down | Ollama is still local to the PC. Start Ollama on the MSI, or let LocalVault AI start it. Do not open port 11434. |

## Privacy and security

This is a same-LAN feature, not a public website.

- Anyone on that Wi-Fi can try to connect. They only get the static page until they enter the code that is on the PC screen.
- The code is short-lived, single-use, and rate-limited. The session that follows is a random token in an HttpOnly cookie. It is kept in memory only. It is tied to the phone's LAN address. It is not put in the URL or the log.
- There is **no HTTPS**. iOS Safari will not trust a homemade certificate without a configuration profile, and a tunnel or a port forward was intentionally not used. On a hostile Wi-Fi, someone who can sniff packets could try to capture a session after you pair. Use a network you trust. Turn iPhone access off when you are done.
- The code runner is still not a sandbox. After pairing, Python started from the phone runs on the PC as your user, same as the ▶ Run button on the PC.
- The PC's browser on `127.0.0.1` does not need the code. A phone cannot use that address; `127.0.0.1` on the phone is the phone itself.
- Host and Origin checks still reject other websites and names that are not the address you bound. That blocks a random web page, and DNS rebinding, from calling the server. There is no wildcard CORS allow-list.
- Path checks for the vendored files are unchanged. The server will not hand out `server.py` or the chat folder as static files.

## Limits (read these)

- Not tested on a real iPhone 17 or on the MSI. Server tests covered unauthenticated denial, hostile Host/Origin, pairing, revoke, loopback-only default, and shutdown. A desktop Chrome window at phone size was used for layout. See the test note that shipped with this update.
- Plain HTTP only. The address bar can say Not Secure. That is expected.
- IPv4 only. Type the IPv4 address from the panel.
- If the phone's Wi-Fi address changes, pair again.
- Add to Home Screen may need its own pairing.
- Closing the app stops listening. It does not remove a firewall rule you added yourself.
- Client isolation on the router cannot be bypassed from this app.
- Do not enable this on a public, guest, or untrusted network.

## Updating the app on the MSI later

The install folder was not known from here. On the PC:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\Install-LocalChatUpdate.ps1 -DryRun
```

The script looks in `%LOCALAPPDATA%\Programs\LocalChat` (this MSI), `%LOCALAPPDATA%\Programs\Local Chat`, `%LOCALAPPDATA%\Local Chat`, `%USERPROFILE%\local-chat-app`, and shortcuts. If it finds several, it stops and asks for `-InstallDir`. It does not guess.

It copies chats and settings to `%LOCALAPPDATA%\LocalChat\backups\<timestamp>\` before it changes program files. It does not delete that data. It will not replace `Local Chat.exe` unless you also pass `-DistDir` pointing at a folder you built on the MSI with `build_exe.bat` (this update does not include a Windows binary). Close LocalVault AI first. The script will not force-quit it. Build and shortcut steps are in [LOCALVAULT.md](LOCALVAULT.md).

Your existing model order is unchanged: chat and images prefer `qwen3.5:4b` then `qwen3.5:2b`, and code prefers `qwen2.5-coder` (the 3b) with `qwen3.5:2b` as the fast fallback.

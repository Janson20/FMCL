using System;
using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;
using PeNet;
using XUserLauncher.Core;

namespace BedrockGdkHelper;

/// <summary>
/// GDK 版 Minecraft 认证注入辅助程序（对齐 BedrockBoot 多用户模式）：
/// 1. 释放 XUserHook.dll（XUserLauncher.LoadDll 从资源提取）并通过 PE 导入表注入游戏 exe
///    （XUserHook 为 pipe-gated 设计：检测到 BedrockBoot.XUser 管道才启用认证 hook，
///    无需 PreLoad.NET.dll，规避其"必须由 BedrockBoot 启动"的校验）
/// 2. 微软账户 access_token → Xbox 认证链（XBL/XSTS/SISU）
/// 3. 挂起启动游戏 + 命名管道传递认证
/// 用法: BedrockGdkHelper <gameFolder> <accessToken> [launchArgs]
/// </summary>
internal static class Program
{
    private const string HookDllName = "XUserHook.dll";
    private const string ConfigDir = "config\\BedrockBoot2";
    private const string RowDir = "config\\BedrockBoot2\\row";

    private static async Task<int> Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("用法: BedrockGdkHelper <游戏目录> <access_token> [启动参数]");
            return 2;
        }
        var gameFolder = Path.GetFullPath(args[0]);
        var accessToken = args[1];
        var launchArgs = args.Length > 2 ? args[2] : "";

        try
        {
            if (!File.Exists(Path.Combine(gameFolder, ConfigDir, "config.json")))
            {
                Console.Error.WriteLine("无效的游戏实例（缺少 config/BedrockBoot2/config.json）");
                return 2;
            }

            var bodyFile = FindBodyFile(gameFolder);
            if (bodyFile == null)
            {
                Console.Error.WriteLine("未找到 Minecraft 主程序");
                return 2;
            }
            var body = Path.Combine(gameFolder, bodyFile);
            var rawBody = Path.Combine(gameFolder, RowDir, bodyFile);
            var preloadDir = Path.Combine(gameFolder, "preload");
            Directory.CreateDirectory(Path.GetDirectoryName(rawBody)!);
            Directory.CreateDirectory(preloadDir);

            // 1. 备份 / 还原原始 exe（避免重复注入；已注入则跳过还原）
            if (!File.Exists(rawBody))
            {
                File.Copy(body, rawBody, true);
                Console.WriteLine($"已备份原始主程序: {bodyFile}");
            }
            var alreadyInjected = FileContains(body, HookDllName);
            if (!alreadyInjected && !FilesEqual(body, rawBody))
            {
                File.Copy(rawBody, body, true);
                Console.WriteLine($"已还原原始主程序: {bodyFile}");
            }

            // 2. 释放 XUserHook.dll（XUserLauncher.LoadDll 从资源提取），复制到 exe 同目录
            //    （exe 导入表按加载目录查找 DLL，preload 子目录不在搜索路径内）
            foreach (var f in Directory.GetFiles(preloadDir))
            {
                try { File.Delete(f); } catch (IOException) { }
            }
            var launcher = new XUserLauncher.Core.XUserLauncher(Path.Combine(gameFolder, ConfigDir, "config.json"));
            launcher.LoadDll();
            var hookPreload = Path.Combine(preloadDir, HookDllName);
            if (!File.Exists(hookPreload))
            {
                Console.Error.WriteLine("XUserHook.dll 释放失败");
                return 2;
            }
            File.Copy(hookPreload, Path.Combine(gameFolder, HookDllName), true);
            Console.WriteLine("XUserHook.dll 已释放到游戏目录");

            // 3. PE 导入表注入 XUserHook.dll（XUserHook 为 pipe-gated：检测到
            //    BedrockBoot.XUser 管道才启用认证 hook）
            if (!alreadyInjected)
            {
                using (var fs = new FileStream(body, FileMode.Open, FileAccess.ReadWrite, FileShare.Read))
                {
                    var peFile = new PeFile(fs);
                    peFile.AddImport(HookDllName, "DllMain");
                    fs.Flush();
                    Console.WriteLine("主程序已注入 XUserHook.dll 导入");
                }
            }
            else
            {
                Console.WriteLine("主程序已注入过 XUserHook.dll，跳过 PE 修改");
            }

            // 5. Xbox 认证链（device → user → xsts/sisu 多 relying party）
            Console.WriteLine("正在进行 Xbox 认证...");
            var preauth = await launcher.AuthenticateAsync(
                JsonSerializer.Serialize(new { access_token = accessToken }));

            // 6. 挂起启动 + 管道传认证 + 恢复（newConsole 显示 XUserHook 日志便于诊断）
            Console.WriteLine("正在启动游戏并注入认证...");
            var proc = SuspendedProcess.Start(body, launchArgs, gameFolder, newConsole: true);
            var injectTask = launcher.LoadInject((int)proc.ProcessId, Environment.ProcessId, preauth, TimeSpan.FromSeconds(120));
            proc.Resume();
            await injectTask;
            Console.WriteLine($"GAME_PID:{proc.ProcessId}");
            // 等待游戏进程退出（游戏退出后 helper 才返回，便于排查）
            proc.WaitForExit(180000);
            Console.WriteLine("GAME_EXITED");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("ERROR: " + ex);
            return 1;
        }
    }

    private static string? FindBodyFile(string gameFolder)
    {
        foreach (var f in Directory.EnumerateFiles(gameFolder, "Minecraft*.exe"))
            return Path.GetFileName(f);
        return null;
    }

    private static bool FilesEqual(string a, string b)
    {
        var fa = new FileInfo(a);
        var fb = new FileInfo(b);
        if (fa.Length != fb.Length) return false;
        using var sha = SHA256.Create();
        using var sa = File.OpenRead(a);
        using var sb = File.OpenRead(b);
        return Convert.ToHexString(sha.ComputeHash(sa)) == Convert.ToHexString(sha.ComputeHash(sb));
    }

    private static bool FileContains(string path, string text)
    {
        var pattern = System.Text.Encoding.ASCII.GetBytes(text);
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024);
        var buffer = new byte[1024 * 1024];
        var overlap = pattern.Length - 1;
        var tail = new byte[overlap];
        int read;
        while ((read = fs.Read(buffer, 0, buffer.Length)) > 0)
        {
            var span = buffer.AsSpan(0, read);
            if (ContainsPattern(span, pattern)) return true;
            if (tail.Length > 0 && read >= overlap)
            {
                buffer.AsSpan(read - overlap, overlap).CopyTo(tail);
            }
            else if (tail.Length > 0 && read > 0)
            {
                // 块小于 overlap 时拼接尾部
                var merged = new byte[tail.Length + read];
                tail.CopyTo(merged, 0);
                buffer.AsSpan(0, read).CopyTo(merged.AsSpan(tail.Length));
                if (ContainsPattern(merged, pattern)) return true;
            }
        }
        return false;
    }

    private static bool ContainsPattern(ReadOnlySpan<byte> data, byte[] pattern)
    {
        for (int i = 0; i <= data.Length - pattern.Length; i++)
        {
            bool match = true;
            for (int j = 0; j < pattern.Length; j++)
            {
                if (data[i + j] != pattern[j]) { match = false; break; }
            }
            if (match) return true;
        }
        return false;
    }
}

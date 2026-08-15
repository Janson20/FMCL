using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using BedrockLauncher.Core;
using BedrockLauncher.Core.CoreOption;
using BedrockLauncher.Core.Utils;

namespace BedrockXvdExtractor;

/// <summary>
/// GDK 版游戏包（XVD/MSIXVC 容器）解压辅助程序
///
/// 底层使用 BedrockLauncher.Core（MIT），与 BedrockBoot 的 GDK 安装解压完全同源：
/// 密钥随官方 NuGet 包分发，本程序不内置、不提取任何密钥。
///
/// 用法: BedrockXvdExtractor &lt;包路径&gt; &lt;输出目录&gt; [release|preview|beta] [--no-hardware]
/// 输出: 每解压一个文件输出一行 "PROGRESS:current/total:相对路径"；
///       结束时输出 "COUNT:n"；错误输出到 stderr，退出码非 0。
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("用法: BedrockXvdExtractor <包路径> <输出目录> [release|preview|beta] [--no-hardware]");
            return 2;
        }
        var packagePath = Path.GetFullPath(args[0]);
        var outputDir = Path.GetFullPath(args[1]);
        var gameType = args.Length > 2 ? args[2].ToLowerInvariant() : "release";
        var useHardware = true;
        for (int i = 3; i < args.Length; i++)
        {
            if (args[i] == "--no-hardware")
                useHardware = false;
        }

        if (!File.Exists(packagePath))
        {
            Console.Error.WriteLine($"包文件不存在: {packagePath}");
            return 2;
        }

        MinecraftGameTypeVersion type = gameType switch
        {
            "preview" => MinecraftGameTypeVersion.Preview,
            "beta" => MinecraftGameTypeVersion.Beta,
            _ => MinecraftGameTypeVersion.Release,
        };

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) =>
        {
            e.Cancel = true;
            cts.Cancel();
        };

        try
        {
            var core = new BedrockCore();
            var fileCount = ExtractAsync(core, packagePath, outputDir, type, useHardware, cts.Token)
                .GetAwaiter().GetResult();
            if (cts.IsCancellationRequested)
            {
                Console.Error.WriteLine("解压已取消");
                return 130;
            }
            Console.WriteLine($"COUNT:{fileCount}");
            return 0;
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("解压已取消");
            return 130;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("ERROR: " + ex);
            return 1;
        }
    }

    private static async Task<long> ExtractAsync(
        BedrockCore core,
        string packagePath,
        string outputDir,
        MinecraftGameTypeVersion gameType,
        bool useHardware,
        CancellationToken token)
    {
        // 硬件加速（AES-NI）失败时（老 CPU 不支持 intrinsics）自动回退软件解密重试
        try
        {
            return await RunExtract(core, packagePath, outputDir, gameType, useHardware, token);
        }
        catch (Exception ex) when (useHardware && IsHardwareDecodeFailure(ex))
        {
            Console.Error.WriteLine("硬件解密不可用，回退软件模式重试: " + ex.Message);
            return await RunExtract(core, packagePath, outputDir, gameType, false, token);
        }
    }

    private static bool IsHardwareDecodeFailure(Exception ex)
    {
        var message = ex.ToString();
        return message.Contains("Intrinsics", StringComparison.OrdinalIgnoreCase)
            || message.Contains("NotSupported", StringComparison.OrdinalIgnoreCase)
            || message.Contains("Aes", StringComparison.OrdinalIgnoreCase);
    }

    private static async Task<long> RunExtract(
        BedrockCore core,
        string packagePath,
        string outputDir,
        MinecraftGameTypeVersion gameType,
        bool useHardware,
        CancellationToken token)
    {
        long fileCount = 0;
        var progress = new Progress<DecompressProgress>(p =>
        {
            fileCount = p.TotalCount;
            Console.WriteLine($"PROGRESS:{p.CurrentCount}/{p.TotalCount}:{p.FileName}");
        });

        await core.InstallPackageAsync(new LocalGamePackageOptions
        {
            FileFullPath = packagePath,
            Type = MinecraftBuildTypeVersion.GDK,
            GameTypeVersion = gameType,
            InstallDstFolder = outputDir,
            UseHardwareDecode = useHardware,
            ExtractionProgress = progress,
            CancellationToken = token,
        });
        return fileCount;
    }
}

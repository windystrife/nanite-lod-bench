// Game target for the Nanite vs LOD bench. Unique build environment: the engine modules are compiled into this
// project's Intermediate folder, so the shared engine tree is not rebuilt.
using UnrealBuildTool;

public class NaniteLodBenchTarget : TargetRules
{
	public NaniteLodBenchTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		BuildEnvironment = TargetBuildEnvironment.Unique;
		ExtraModuleNames.Add("NaniteLodBench");
		// No listening sockets => no Windows Firewall prompt on a new exe path:
		// TraceLog's TCP control server (port 1985, all interfaces) is compiled out. File tracing (-tracefile) still works.
		GlobalDefinitions.Add("UE_TRACE_ALLOW_TCP_CONTROL=0");
		// Never auto-launch <EngineDir>/Binaries/Win64/UnrealTraceServer.exe (it listens on 1981/1989): a Development game does
		// that at trace init unless -notraceserver (TraceAuxiliary.cpp:39-40, 2363-2369, 2784-2791). Not staged in a package,
		// but a run from the project folder would find the engine copy.
		GlobalDefinitions.Add("UE_TRACE_SERVER_LAUNCH_ENABLED=0");
	}
}

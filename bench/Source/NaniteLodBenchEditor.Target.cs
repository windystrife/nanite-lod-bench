// Editor target exists only so UAT recognises the project (ProjectParams.cs:2768-2777 throws "Editor target not found!" for a
// code project without one). NEVER build it: it is a Shared-environment target, so building it relinks engine binaries
// (BuildCookRun must get -nocompileeditor, else BuildProjectCommand.Automation.cs:90-96 builds it). The editor/cooker runs on the
// engine's existing UnrealEditor binaries (the runtime module is denied for Editor in the .uproject).
// BuildCookRun must ALSO get -unrealexe=<engine>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe: without it UAT resolves the editor
// from this target's receipt <Project>\Binaries\Win64\NaniteLodBenchEditor.target (ProjectParams.cs:1709-1718,
// ProjectUtils.cs:708-722), which never exists, and the open throws FileNotFound (JsonObject.cs:258-260).
using UnrealBuildTool;

public class NaniteLodBenchEditorTarget : TargetRules
{
	public NaniteLodBenchEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		// No ExtraModuleNames: the bench module is game-only (TargetDenyList Editor in the .uproject).
	}
}

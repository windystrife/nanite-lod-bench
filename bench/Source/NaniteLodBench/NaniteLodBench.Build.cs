// Nanite vs LOD bench (replicates crdg.jp "Nanite vs LOD" article) on the Zone9 UE 5.8 engine.
using UnrealBuildTool;

public class NaniteLodBench : ModuleRules
{
	public NaniteLodBench(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
		PrivateDependencyModuleNames.AddRange(new string[] { "RenderCore", "RHI", "Json" });
	}
}

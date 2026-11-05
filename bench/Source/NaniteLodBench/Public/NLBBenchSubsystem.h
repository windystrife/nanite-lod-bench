#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "NLBBenchSubsystem.generated.h"

class ACameraActor;
class UStaticMesh;
class UStaticMeshComponent;
class UInstancedStaticMeshComponent;

/**
 * Nanite-vs-LOD bench driver (replicates the crdg.jp "Nanite vs LOD" test on the Zone9 UE 5.8 engine).
 *
 * Runs only in game worlds. Command line (all optional):
 *   -NLBAsset=Table|Rock -NLBMode=LOD|Nanite|Raw   picks /Game/NLB/SM_<Asset>_<Mode> (default Table, LOD)
 *   -NLBMesh=/Game/NLB/SM_Table_LOD.SM_Table_LOD   explicit mesh path (overrides Asset/Mode)
 *   -NLBScale=1.0                                  uniform scale of every copy (Rock ~0.4 matches a 2 m table)
 *   -NLBLayout=Actors|ISM                          one AStaticMeshActor per copy (article) or one ISM component
 *   -NLBCounts=1,2048,4096,8192                    copies, grown in place (copies are added, never removed)
 *   -NLBBookmarks=0,1,2,3,4,5,6,7                  camera bookmarks measured per count: 0 close (centre copy from the
 *                                                  aisle), 1 edge_eye, 2 mid_high, 3 overview, 4 far, 5 extreme, 6 topdown, 7 sky
 *   -NLBUnlit=1                                    after the last count, measure every bookmark again unlit (VMI_Unlit applied
 *                                                  to the game viewport; the "viewmode" command is refused in a cooked build)
 *   -NLBUnlitRich=0                                0 (default): unlit keeps every mesh on its normal draw path. 1: also keep the
 *                                                  UnlitRichView show flag = UE's own Unlit view mode, which in a non-shipping build
 *                                                  forces classic static meshes / ISM (LOD, Raw arms) onto the per-frame dynamic path
 *                                                  (IsRichView, StaticMeshSceneProxy.cpp:2462) while Nanite ignores it
 *                                                  (NaniteResources.cpp:1332) - an asymmetric debug-path cost
 *   -NLBBoot=8 -NLBSpawnSettle=6 -NLBSettle=3 -NLBMeasure=5 -NLBWarmLap=0.7   seconds (Settle also waits >= 5 frames)
 *   -NLBMaxRun=900                                 seconds; past it the run aborts ("complete":false, exit code 4); 0 = no cap
 *   -NLBSpacing=1.3                                cell = Spacing x max(mesh XY size); copies are centred on their bounds
 *   -NLBShots=1                                    one screenshot per bookmark at the last count (lit and unlit)
 *   -NLBSG=0                                       scalability level for every group (-1 = leave as is)
 *   -NLBCvars="r.ScreenPercentage=100;r.VSync=0"   console variables set after scalability (SetByConsole)
 *   -NLBCsv=1                                      CSV profiler capture, events "<segment>" and "<segment>_end" per segment
 *   -NLBOut=<dir> -NLBTag=<name>                   where results go (default <ProjectSavedDir>/NLB, which follows -UserDir;
 *                                                  default tag <Asset>_<Mode>_<Layout>, e.g. Table_LOD_Actors)
 *   -NLBNoExit                                     stay running at the end (debug)
 *   -NLBOff                                        do not create the bench subsystem at all
 * Output: <Out>/<Tag>_summary.json ("complete":false + "abort_reason" if the run was cut short), <Tag>_segments.csv,
 *         <Tag>_frames.csv, <Tag>_profile.csv[.gz] (CSV profiler), <Tag>_shots/<segment>.png, log lines "NLBSEG ...", "NLB_DONE ..."
 *         or "NLB_ABORTED ... reason=...". Exit code 0 = done, 3 = mesh not found, 4 = -NLBMaxRun hit, 5 = no player camera.
 */
UCLASS()
class UNLBBenchSubsystem : public UTickableWorldSubsystem
{
	GENERATED_BODY()

public:
	virtual bool ShouldCreateSubsystem(UObject* Outer) const override;
	virtual void OnWorldBeginPlay(UWorld& InWorld) override;
	virtual void Deinitialize() override;
	virtual void Tick(float DeltaTime) override;
	virtual TStatId GetStatId() const override;

private:
	enum class EPhase : uint8 { Idle, Boot, Spawn, SpawnSettle, WarmLap, Settle, Measure, Shot, Finish, Exiting };

	struct FBookmark
	{
		FString Name;
		FVector Location = FVector::ZeroVector;
		FRotator Rotation = FRotator::ZeroRotator;
	};

	struct FFrameSample
	{
		float FrameMs = 0, GameMs = 0, RenderMs = 0, RHIMs = 0, GPUMs = 0;
		int32 DrawCalls = 0, Primitives = 0;
	};

	struct FSegment
	{
		int32 Count = 0;
		int32 Bookmark = 0;
		bool bUnlit = false;
		FString Name;
		double StartTime = 0, EndTime = 0;
		TArray<FFrameSample> Frames;
	};

	void ParseCommandLine();
	void ApplyRenderSettings();
	void BuildBookmarks();
	void BuildPlan();
	bool EnsureCamera();
	void SetBookmark(int32 Index);
	void SpawnUpTo(int32 Count);
	void SetUnlit(bool bUnlit);
	void SampleFrame(float DeltaTime);
	void FinishSegment();
	void WriteResults(bool bComplete);
	void AbortRun(const TCHAR* Reason);
	void LogMeshInfo();
	int32 CountNaniteProxies() const;
	void EnterPhase(EPhase NewPhase);
	double PhaseAge() const;

	// Settings
	FString MeshPath;
	FString AssetName = TEXT("Table");
	FString Mode = TEXT("LOD");
	bool bISM = false;
	TArray<int32> Counts;
	TArray<int32> BookmarkIds;
	bool bUnlitPass = true;
	bool bUnlitRichView = false;
	double BootSec = 8.0, SpawnSettleSec = 6.0, SettleSec = 3.0, MeasureSec = 5.0, WarmLapSec = 0.7;
	double MaxRunSec = 900.0;
	double SpacingFactor = 1.3;
	float MeshScale = 1.f;
	bool bShots = true;
	int32 ScalabilityLevel = 0;
	FString CvarList;
	bool bCsv = true;
	FString OutDir;
	FString Tag;
	bool bNoExit = false;

	// State
	bool bActive = false;
	EPhase Phase = EPhase::Idle;
	double PhaseStart = 0;
	int32 PhaseFrames = 0; // ticks since EnterPhase
	uint8 AbortExitCode = 0;
	FString AbortReason;
	UPROPERTY() TObjectPtr<UStaticMesh> Mesh;
	UPROPERTY() TObjectPtr<ACameraActor> Camera;
	UPROPERTY() TArray<TObjectPtr<UStaticMeshComponent>> Components;
	UPROPERTY() TObjectPtr<UInstancedStaticMeshComponent> ISMComponent;
	TArray<FTransform> GridTransforms; // ordered by ring distance from the grid centre
	FVector CellSize = FVector::ZeroVector;
	double GridHalf = 0;
	double MeshHeight = 0;
	TArray<FBookmark> Bookmarks;
	TArray<FSegment> Segments; // the plan, filled in as it runs
	int32 SegIndex = -1;
	int32 SpawnedCount = 0;
	int32 WarmLapIndex = 0;
	bool bUnlitActive = false;
	TArray<FString> SpawnLog; // "count=N ms=X"
	TArray<FString> NaniteProxyLog;
	FString MeshInfoJson;
	double RunStart = 0;
	int32 ExitFrames = 0;
	bool bCsvStarted = false;
	FString CsvFile;
};

#include "NLBBenchSubsystem.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Camera/PlayerCameraManager.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/EngineBaseTypes.h"
#include "Engine/GameViewportClient.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "PrimitiveSceneProxy.h"
#include "ProfilingDebugging/CsvProfiler.h"
#include "ProfilingDebugging/MiscTrace.h"
#include "RenderCore.h"
#include "RenderTimer.h"
#include "RenderUtils.h"
#include "RHI.h"
#include "RHIStats.h"
#include "Rendering/NaniteResources.h"
#include "Scalability.h"
#include "ShowFlags.h"
#include "StaticMeshResources.h"
#include "UnrealClient.h"

DEFINE_LOG_CATEGORY_STATIC(LogNLB, Log, All);

#if CSV_PROFILER
CSV_DEFINE_CATEGORY(NLB, true);
#endif

namespace NLB
{
	// Settle also needs this many ticks: GPU time and draw counters are read back 2-3 frames late (GPUProfiler.cpp:2099-2102,
	// :2267-2279), so at very low fps a pure 3 s settle could hand the first Measure samples counters of the previous bookmark.
	static constexpr int32 MinSettleFrames = 5;

	static float Percentile(TArray<float> V, double P)
	{
		if (V.Num() == 0) { return 0.f; }
		V.Sort();
		const double Pos = P * (V.Num() - 1);
		const int32 Lo = FMath::FloorToInt32(Pos);
		const int32 Hi = FMath::Min(Lo + 1, V.Num() - 1);
		const double T = Pos - Lo;
		return float(V[Lo] * (1.0 - T) + V[Hi] * T);
	}

	static float Mean(const TArray<float>& V)
	{
		if (V.Num() == 0) { return 0.f; }
		double S = 0; for (float X : V) { S += X; }
		return float(S / V.Num());
	}

	static FString JsonEscape(const FString& In)
	{
		FString Out = In.Replace(TEXT("\\"), TEXT("\\\\")).Replace(TEXT("\""), TEXT("\\\""));
		Out.ReplaceInline(TEXT("\r"), TEXT("\\r"));
		Out.ReplaceInline(TEXT("\n"), TEXT("\\n"));
		Out.ReplaceInline(TEXT("\t"), TEXT("\\t"));
		return Out;
	}

	static FString CvarString(const TCHAR* Name)
	{
		IConsoleVariable* V = IConsoleManager::Get().FindConsoleVariable(Name);
		return V ? V->GetString() : FString(TEXT("<missing>"));
	}

	static const TCHAR* GInterestingCvars[] = {
		TEXT("r.Nanite"), TEXT("r.Nanite.ProjectEnabled"), TEXT("r.Nanite.MaxPixelsPerEdge"), TEXT("r.Nanite.Culling.TwoPass"),
		TEXT("r.HZBOcclusion"), TEXT("r.NumBufferedOcclusionQueries"), TEXT("r.AllowOcclusionQueries"),
		TEXT("r.ScreenPercentage"), TEXT("r.AntiAliasingMethod"), TEXT("r.DynamicGlobalIlluminationMethod"), TEXT("r.ReflectionMethod"),
		TEXT("r.Shadow.Virtual.Enable"), TEXT("r.Shadow.CSM.MaxCascades"), TEXT("r.Shadow.DistanceScale"),
		TEXT("r.StaticMeshLODDistanceScale"), TEXT("r.ViewDistanceScale"), TEXT("r.ForceLOD"),
		TEXT("r.MeshDrawCommands.DynamicInstancing"), TEXT("r.MeshDrawCommands.ParallelPassSetup"),
		TEXT("r.VSync"), TEXT("t.MaxFPS"), TEXT("r.FullScreenMode"), TEXT("r.GPUCsvStatsEnabled"),
		TEXT("sg.ResolutionQuality"), TEXT("sg.ViewDistanceQuality"), TEXT("sg.ShadowQuality"), TEXT("sg.GlobalIlluminationQuality"),
		TEXT("sg.PostProcessQuality"), TEXT("sg.EffectsQuality"), TEXT("sg.FoliageQuality"), TEXT("sg.TextureQuality"), TEXT("sg.ShadingQuality"),
	};
}

bool UNLBBenchSubsystem::ShouldCreateSubsystem(UObject* Outer) const
{
	const UWorld* World = Cast<UWorld>(Outer);
	if (!World || !(World->WorldType == EWorldType::Game || World->WorldType == EWorldType::PIE))
	{
		return false;
	}
	return !FParse::Param(FCommandLine::Get(), TEXT("NLBOff")) && Super::ShouldCreateSubsystem(Outer);
}

TStatId UNLBBenchSubsystem::GetStatId() const
{
	RETURN_QUICK_DECLARE_CYCLE_STAT(UNLBBenchSubsystem, STATGROUP_Tickables);
}

void UNLBBenchSubsystem::ParseCommandLine()
{
	const TCHAR* Cmd = FCommandLine::Get();
	FString S;

	if (FParse::Value(Cmd, TEXT("NLBMode="), S)) { Mode = S; }
	Mode = Mode.Equals(TEXT("Nanite"), ESearchCase::IgnoreCase) ? TEXT("Nanite") : Mode.Equals(TEXT("Raw"), ESearchCase::IgnoreCase) ? TEXT("Raw") : TEXT("LOD");
	FString Asset = TEXT("Table");
	if (FParse::Value(Cmd, TEXT("NLBAsset="), S)) { Asset = S.Equals(TEXT("Rock"), ESearchCase::IgnoreCase) ? TEXT("Rock") : TEXT("Table"); }
	AssetName = Asset;
	MeshPath = FString::Printf(TEXT("/Game/NLB/SM_%s_%s.SM_%s_%s"), *Asset, *Mode, *Asset, *Mode);
	if (FParse::Value(Cmd, TEXT("NLBMesh="), S)) { MeshPath = S; }
	float Scale = 1.f;
	if (FParse::Value(Cmd, TEXT("NLBScale="), Scale) && Scale > 0.f) { MeshScale = Scale; }
	if (FParse::Value(Cmd, TEXT("NLBLayout="), S)) { bISM = S.Equals(TEXT("ISM"), ESearchCase::IgnoreCase); }

	Counts = { 1, 2048, 4096, 8192 };
	if (FParse::Value(Cmd, TEXT("NLBCounts="), S, /*bShouldStopOnSeparator*/ false))
	{
		TArray<FString> Parts; S.ParseIntoArray(Parts, TEXT(","), true);
		Counts.Reset();
		for (const FString& P : Parts) { const int32 N = FCString::Atoi(*P); if (N > 0) { Counts.Add(N); } }
		Counts.Sort();
	}
	if (Counts.Num() == 0) { Counts.Add(1); }

	BookmarkIds = { 0, 1, 2, 3, 4, 5, 6, 7 };
	if (FParse::Value(Cmd, TEXT("NLBBookmarks="), S, false))
	{
		TArray<FString> Parts; S.ParseIntoArray(Parts, TEXT(","), true);
		BookmarkIds.Reset();
		for (const FString& P : Parts) { const int32 B = FCString::Atoi(*P); if (B >= 0 && B <= 7) { BookmarkIds.Add(B); } }
	}
	if (BookmarkIds.Num() == 0) { BookmarkIds.Add(0); }

	int32 I = 0;
	if (FParse::Value(Cmd, TEXT("NLBUnlit="), I)) { bUnlitPass = I != 0; }
	if (FParse::Value(Cmd, TEXT("NLBUnlitRich="), I)) { bUnlitRichView = I != 0; }
	if (FParse::Value(Cmd, TEXT("NLBShots="), I)) { bShots = I != 0; }
	if (FParse::Value(Cmd, TEXT("NLBCsv="), I)) { bCsv = I != 0; }
	if (FParse::Value(Cmd, TEXT("NLBSG="), I)) { ScalabilityLevel = I; }
	float F = 0.f;
	if (FParse::Value(Cmd, TEXT("NLBBoot="), F)) { BootSec = F; }
	if (FParse::Value(Cmd, TEXT("NLBSpawnSettle="), F)) { SpawnSettleSec = F; }
	if (FParse::Value(Cmd, TEXT("NLBSettle="), F)) { SettleSec = F; }
	if (FParse::Value(Cmd, TEXT("NLBMeasure="), F)) { MeasureSec = F; }
	if (FParse::Value(Cmd, TEXT("NLBWarmLap="), F)) { WarmLapSec = F; }
	if (FParse::Value(Cmd, TEXT("NLBSpacing="), F)) { SpacingFactor = FMath::Max(1.0f, F); }
	if (FParse::Value(Cmd, TEXT("NLBMaxRun="), F)) { MaxRunSec = F; }
	if (FParse::Value(Cmd, TEXT("NLBCvars="), S, false)) { CvarList = S; }
	bNoExit = FParse::Param(Cmd, TEXT("NLBNoExit"));

	Tag = FString::Printf(TEXT("%s_%s_%s"), *Asset, *Mode, bISM ? TEXT("ISM") : TEXT("Actors"));
	if (FParse::Value(Cmd, TEXT("NLBTag="), S)) { Tag = S; }
	OutDir = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("NLB"));
	if (FParse::Value(Cmd, TEXT("NLBOut="), S)) { OutDir = S; }
	// ProjectSavedDir() is relative ("../../../<Project>/Saved/") in a packaged game; log/record the absolute path.
	OutDir = FPaths::ConvertRelativePathToFull(OutDir);
	FPaths::NormalizeDirectoryName(OutDir);
	IFileManager::Get().MakeDirectory(*OutDir, true);
}

void UNLBBenchSubsystem::ApplyRenderSettings()
{
	if (ScalabilityLevel >= 0)
	{
		Scalability::FQualityLevels Q = Scalability::GetQualityLevels();
		Q.SetFromSingleQualityLevel(ScalabilityLevel);
		Scalability::SetQualityLevels(Q, /*bForce*/ true);
		UE_LOG(LogNLB, Display, TEXT("NLB scalability set to %d"), ScalabilityLevel);
	}

	TArray<FString> Pairs;
	CvarList.ParseIntoArray(Pairs, TEXT(";"), true);
	for (const FString& Pair : Pairs)
	{
		FString Name, Value;
		if (!Pair.Split(TEXT("="), &Name, &Value)) { continue; }
		Name.TrimStartAndEndInline(); Value.TrimStartAndEndInline();
		if (IConsoleVariable* V = IConsoleManager::Get().FindConsoleVariable(*Name))
		{
			V->Set(*Value, ECVF_SetByConsole);
			UE_LOG(LogNLB, Display, TEXT("NLB cvar %s=%s (now %s)"), *Name, *Value, *V->GetString());
		}
		else
		{
			UE_LOG(LogNLB, Warning, TEXT("NLB cvar %s not found"), *Name);
		}
	}
}

void UNLBBenchSubsystem::OnWorldBeginPlay(UWorld& InWorld)
{
	Super::OnWorldBeginPlay(InWorld);

	ParseCommandLine();
	ApplyRenderSettings();

	Mesh = LoadObject<UStaticMesh>(nullptr, *MeshPath);
	if (!Mesh)
	{
		UE_LOG(LogNLB, Error, TEXT("NLB_FATAL mesh not found: %s"), *MeshPath);
		FFileHelper::SaveStringToFile(FString::Printf(TEXT("{\"error\":\"mesh not found\",\"mesh\":\"%s\"}\n"), *NLB::JsonEscape(MeshPath)),
			*FPaths::Combine(OutDir, Tag + TEXT("_summary.json")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
		if (!bNoExit) { FPlatformMisc::RequestExitWithStatus(false, 3); }
		return;
	}

	// Grid: K x K cells (K = ceil(sqrt(max count))), filled ring by ring from the centre, so every count is a centred square
	// and the bookmarks (fixed for the whole run) look at the same place for every count.
	const FBox Box0 = Mesh->GetBoundingBox();
	const FBox Box(Box0.Min * MeshScale, Box0.Max * MeshScale);
	const FVector Size = Box.GetSize();
	const double Cell = SpacingFactor * FMath::Max(Size.X, Size.Y);
	CellSize = FVector(Cell, Cell, Size.Z);
	MeshHeight = Size.Z;
	const int32 MaxCount = Counts.Last();
	int32 K = FMath::CeilToInt32(FMath::Sqrt(double(MaxCount)));
	// Odd K: one cell sits exactly on the origin, so count 1 is the copy every bookmark aims at (8192 -> 91, unchanged).
	if ((K & 1) == 0) { ++K; }
	GridHalf = 0.5 * K * Cell;
	const double C = 0.5 * (K - 1);
	struct FCellRef { int32 X, Y; double Ring, Angle; };
	TArray<FCellRef> Cells;
	Cells.Reserve(K * K);
	for (int32 X = 0; X < K; ++X)
	{
		for (int32 Y = 0; Y < K; ++Y)
		{
			const double DX = X - C, DY = Y - C;
			Cells.Add({ X, Y, FMath::Max(FMath::Abs(DX), FMath::Abs(DY)), FMath::Atan2(DY, DX) });
		}
	}
	Cells.Sort([](const FCellRef& A, const FCellRef& B) { return A.Ring != B.Ring ? A.Ring < B.Ring : A.Angle < B.Angle; });
	GridTransforms.Reset();
	// Centre each copy's (scaled) bounds on its cell, not its pivot: a scan/rock pivot is often off-centre, and with only
	// (Spacing-1) x size of slack per cell an off-centre pivot makes neighbours overlap. Bottom of the bounds on Z = 0.
	const FVector BoxCentre = Box.GetCenter();
	for (int32 i = 0; i < MaxCount && i < Cells.Num(); ++i)
	{
		const FVector Loc((Cells[i].X - C) * Cell - BoxCentre.X, (Cells[i].Y - C) * Cell - BoxCentre.Y, -Box.Min.Z);
		GridTransforms.Add(FTransform(FRotator::ZeroRotator, Loc, FVector(MeshScale)));
	}

	BuildBookmarks();
	BuildPlan();
	LogMeshInfo();

	RunStart = FPlatformTime::Seconds();
	bActive = true;
	EnterPhase(EPhase::Boot);
	UE_LOG(LogNLB, Display, TEXT("NLB start tag=%s mode=%s layout=%s mesh=%s counts=%d..%d segments=%d grid K=%d cell=%.1f half=%.1f"),
		*Tag, *Mode, bISM ? TEXT("ISM") : TEXT("Actors"), *MeshPath, Counts[0], Counts.Last(), Segments.Num(), K, Cell, GridHalf);
}

void UNLBBenchSubsystem::BuildBookmarks()
{
	const double H = GridHalf;
	const double Cell = CellSize.X;
	auto LookAt = [](const FVector& From, const FVector& To) { return (To - From).Rotation(); };
	const FVector Centre(0, 0, MeshHeight * 0.5);

	Bookmarks.Reset();
	// B0: eye level in the aisle corner next to the centre copy, looking diagonally across it into the grid. (It used to stand
	// at (+0.5, +0.5) cells looking +X, i.e. with the centre copy behind the camera: at count 1 it rendered no copy at all.)
	Bookmarks.Add({ TEXT("B0_close"),   FVector(-0.5 * Cell, 0.5 * Cell, FMath::Max(160.0, MeshHeight + 80.0)), FRotator(-15.0, -45.0, 0.0) });
	Bookmarks.Add({ TEXT("B1_edge_eye"), FVector(-H - 2.0 * Cell, 0.0, 170.0), FRotator(-5.0, 0.0, 0.0) });
	{
		const FVector L(-H, -H, 0.25 * H);
		Bookmarks.Add({ TEXT("B2_mid_high"), L, LookAt(L, Centre) });
	}
	{
		const FVector L(-1.3 * H, -1.3 * H, 1.1 * H);
		Bookmarks.Add({ TEXT("B3_overview"), L, LookAt(L, Centre) });
	}
	{
		const FVector L(-3.0 * H, 0.0, 0.5 * H);
		Bookmarks.Add({ TEXT("B4_far"), L, LookAt(L, Centre) });
	}
	{
		const FVector L(-8.0 * H, 0.0, 1.0 * H);
		Bookmarks.Add({ TEXT("B5_extreme"), L, LookAt(L, Centre) });
	}
	Bookmarks.Add({ TEXT("B6_topdown"), FVector(0.0, 0.0, 1.4 * H), FRotator(-89.0, 0.0, 0.0) });
	// Above the centre copy (never inside a tall mesh, e.g. a rock at -NLBScale=1), looking up: the "nothing on screen" baseline.
	Bookmarks.Add({ TEXT("B7_sky"),     FVector(0.0, 0.0, FMath::Max(170.0, MeshHeight + 90.0)), FRotator(45.0, 0.0, 0.0) });
}

void UNLBBenchSubsystem::BuildPlan()
{
	Segments.Reset();
	auto AddSeg = [this](int32 Count, int32 B, bool bUnlit)
	{
		FSegment S;
		S.Count = Count; S.Bookmark = B; S.bUnlit = bUnlit;
		S.Name = FString::Printf(TEXT("n%05d_%s%s"), Count, *Bookmarks[B].Name, bUnlit ? TEXT("_unlit") : TEXT(""));
		Segments.Add(MoveTemp(S));
	};
	for (int32 Count : Counts) { for (int32 B : BookmarkIds) { AddSeg(Count, B, false); } }
	if (bUnlitPass) { for (int32 B : BookmarkIds) { AddSeg(Counts.Last(), B, true); } }
	SegIndex = -1;
}

bool UNLBBenchSubsystem::EnsureCamera()
{
	UWorld* World = GetWorld();
	APlayerController* PC = World ? World->GetFirstPlayerController() : nullptr;
	if (!PC) { return false; }
	if (!Camera)
	{
		FActorSpawnParameters P;
		P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		Camera = World->SpawnActor<ACameraActor>(ACameraActor::StaticClass(), FTransform::Identity, P);
		if (!Camera) { return false; }
		UCameraComponent* CamComp = Camera->GetCameraComponent();
		CamComp->SetFieldOfView(90.f);
		CamComp->SetConstraintAspectRatio(false);
	}
	if (PC->GetViewTarget() != Camera)
	{
		PC->bAutoManageActiveCameraTarget = false;
		PC->SetViewTargetWithBlend(Camera, 0.f);
	}
	return true;
}

void UNLBBenchSubsystem::SetBookmark(int32 Index)
{
	if (!Camera || !Bookmarks.IsValidIndex(Index)) { return; }
	Camera->SetActorLocationAndRotation(Bookmarks[Index].Location, Bookmarks[Index].Rotation);
	if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
	{
		if (PC->PlayerCameraManager) { PC->PlayerCameraManager->SetGameCameraCutThisFrame(); }
	}
}

void UNLBBenchSubsystem::SpawnUpTo(int32 Count)
{
	UWorld* World = GetWorld();
	Count = FMath::Min(Count, GridTransforms.Num());
	if (Count <= SpawnedCount) { return; }
	const double T0 = FPlatformTime::Seconds();

	if (bISM)
	{
		if (!ISMComponent)
		{
			FActorSpawnParameters P;
			P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			AActor* Holder = World->SpawnActor<AActor>(AActor::StaticClass(), FTransform::Identity, P);
			if (!Holder) { UE_LOG(LogNLB, Error, TEXT("NLB_FATAL cannot spawn the ISM holder actor")); return; }
			ISMComponent = NewObject<UInstancedStaticMeshComponent>(Holder, TEXT("NLB_ISM"));
			ISMComponent->SetMobility(EComponentMobility::Static);
			ISMComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			ISMComponent->SetStaticMesh(Mesh);
			Holder->SetRootComponent(ISMComponent);
			ISMComponent->RegisterComponent();
		}
		TArray<FTransform> NewOnes;
		for (int32 i = SpawnedCount; i < Count; ++i) { NewOnes.Add(GridTransforms[i]); }
		ISMComponent->AddInstances(NewOnes, /*bShouldReturnIndices*/ false, /*bWorldSpace*/ true);
	}
	else
	{
		FActorSpawnParameters P;
		P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		for (int32 i = SpawnedCount; i < Count; ++i)
		{
			AStaticMeshActor* A = World->SpawnActor<AStaticMeshActor>(AStaticMeshActor::StaticClass(), GridTransforms[i], P);
			if (!A) { continue; }
			UStaticMeshComponent* SMC = A->GetStaticMeshComponent();
			// A registered Static component refuses SetStaticMesh after BeginPlay; unregistered it is allowed.
			SMC->UnregisterComponent();
			SMC->SetMobility(EComponentMobility::Static);
			SMC->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			SMC->SetStaticMesh(Mesh);
			SMC->RegisterComponent();
			Components.Add(SMC);
		}
	}

	const double Ms = (FPlatformTime::Seconds() - T0) * 1000.0;
	SpawnLog.Add(FString::Printf(TEXT("{\"from\":%d,\"to\":%d,\"ms\":%.1f}"), SpawnedCount, Count, Ms));
	UE_LOG(LogNLB, Display, TEXT("NLB spawned %d -> %d copies in %.1f ms"), SpawnedCount, Count, Ms);
	SpawnedCount = Count;
}

int32 UNLBBenchSubsystem::CountNaniteProxies() const
{
	if (bISM)
	{
		return (ISMComponent && ISMComponent->SceneProxy && ISMComponent->SceneProxy->IsNaniteMesh()) ? ISMComponent->GetInstanceCount() : 0;
	}
	int32 N = 0;
	for (const TObjectPtr<UStaticMeshComponent>& C : Components)
	{
		if (C && C->SceneProxy && C->SceneProxy->IsNaniteMesh()) { ++N; }
	}
	return N;
}

void UNLBBenchSubsystem::SetUnlit(bool bUnlit)
{
	// Not "viewmode unlit": in a cooked build UGameViewportClient::SetViewMode forces every mode but Lit/ShaderComplexity back
	// to Lit because AllowDebugViewmodes() is false when FPlatformProperties::RequiresCookedData() (GameViewportClient.cpp
	// :4747-4754, ShaderCore.cpp:579-610), and HandleViewModeCommand also needs a CheatManager (:3981-3997). Do what SetViewMode
	// does once the mode is accepted (:4788): set the index and apply it to the viewport show flags. Draw() then derives the
	// per-frame flags from it (EngineShowFlagOverride, :1623-1624; Lighting/Atmosphere/Fog off, ShowFlags.cpp:564-591), and the
	// base pass writes BaseColor (UnlitViewmodeMask, SceneView.cpp:2911, BasePassPixelShader.usf:1623-1628, Development cook).
	const EViewModeIndex NewMode = bUnlit ? VMI_Unlit : VMI_Lit;
	UGameViewportClient* GVC = GetWorld() ? GetWorld()->GetGameViewport() : nullptr;
	if (!GVC && GEngine) { GVC = GEngine->GameViewport; }
	if (GVC)
	{
		GVC->ViewModeIndex = NewMode;
		ApplyViewMode(NewMode, /*bPerspective*/ true, GVC->EngineShowFlags);
		// Lighting off + UnlitRichView (on by default, ShowFlags.h:392-394) makes IsRichView() true (PrimitiveDrawingUtils.cpp:1585),
		// which in a non-shipping build moves FStaticMeshSceneProxy (LOD/Raw arms, ISM too) to dynamic relevance, i.e. per-frame
		// GetDynamicMeshElements instead of cached draw commands (StaticMeshSceneProxy.cpp:2460-2482), while the Nanite proxy
		// ignores rich view (NaniteResources.cpp:1331-1332). Off by default so "unlit" removes lighting only; -NLBUnlitRich=1
		// reproduces the editor/"viewmode unlit" behaviour. UnlitViewmode (BaseColor output, SceneView.cpp:2911) is untouched.
		GVC->EngineShowFlags.SetUnlitRichView(!bUnlit || bUnlitRichView);
	}
	bUnlitActive = bUnlit;
	UE_LOG(LogNLB, Display, TEXT("NLB viewmode %s rich_view=%d (%s)"), bUnlit ? TEXT("unlit") : TEXT("lit"), (!bUnlit || bUnlitRichView) ? 1 : 0,
		GVC ? TEXT("applied") : TEXT("NO GAME VIEWPORT"));
}

void UNLBBenchSubsystem::LogMeshInfo()
{
	FString Lods;
	if (const FStaticMeshRenderData* RD = Mesh->GetRenderData())
	{
		for (int32 L = 0; L < RD->LODResources.Num(); ++L)
		{
			Lods += FString::Printf(TEXT("%s{\"lod\":%d,\"tris\":%d,\"verts\":%d,\"screen_size\":%.4f}"),
				L ? TEXT(",") : TEXT(""), L, RD->LODResources[L].GetNumTriangles(), RD->LODResources[L].GetNumVertices(),
				RD->ScreenSize[L].Default);
		}
		int64 NaniteTris = 0;
		if (RD->HasValidNaniteData() && RD->NaniteResourcesPtr.IsValid())
		{
			NaniteTris = RD->NaniteResourcesPtr->NumInputTriangles;
		}
		MeshInfoJson = FString::Printf(TEXT("{\"path\":\"%s\",\"has_valid_nanite_data\":%s,\"nanite_input_tris\":%lld,\"bounds_cm\":[%.1f,%.1f,%.1f],\"lods\":[%s]}"),
			*NLB::JsonEscape(MeshPath), RD->HasValidNaniteData() ? TEXT("true") : TEXT("false"), NaniteTris,
			Mesh->GetBoundingBox().GetSize().X, Mesh->GetBoundingBox().GetSize().Y, Mesh->GetBoundingBox().GetSize().Z, *Lods);
	}
	else
	{
		MeshInfoJson = TEXT("{\"error\":\"no render data\"}");
	}
	UE_LOG(LogNLB, Display, TEXT("NLB mesh %s"), *MeshInfoJson);
	UE_LOG(LogNLB, Display, TEXT("NLB platform: RHI=%s adapter=%s driver=%s UseNanite=%d PlatformSupportsNanite=%d Atomics64=%d"),
		GDynamicRHI ? GDynamicRHI->GetName() : TEXT("?"), *GRHIAdapterName, *GRHIAdapterUserDriverVersion,
		UseNanite(GMaxRHIShaderPlatform) ? 1 : 0, DoesPlatformSupportNanite(GMaxRHIShaderPlatform) ? 1 : 0, GRHISupportsAtomicUInt64 ? 1 : 0);
}

void UNLBBenchSubsystem::EnterPhase(EPhase NewPhase)
{
	Phase = NewPhase;
	PhaseStart = FPlatformTime::Seconds();
	PhaseFrames = 0;
}

double UNLBBenchSubsystem::PhaseAge() const
{
	return FPlatformTime::Seconds() - PhaseStart;
}

void UNLBBenchSubsystem::SampleFrame(float DeltaTime)
{
	if (!Segments.IsValidIndex(SegIndex)) { return; }
	FFrameSample F;
	F.FrameMs = float(FApp::GetDeltaTime() * 1000.0);
	F.GameMs = float(FPlatformTime::ToMilliseconds(GGameThreadTime));
	F.RenderMs = float(FPlatformTime::ToMilliseconds(GRenderThreadTime));
	F.RHIMs = float(FPlatformTime::ToMilliseconds(GRHIThreadTime));
	F.GPUMs = float(FPlatformTime::ToMilliseconds(RHIGetGPUFrameCycles(0)));
	F.DrawCalls = GNumDrawCallsRHI[0];
	F.Primitives = GNumPrimitivesDrawnRHI[0];
	Segments[SegIndex].Frames.Add(F);
}

void UNLBBenchSubsystem::FinishSegment()
{
	FSegment& S = Segments[SegIndex];
	S.EndTime = FPlatformTime::Seconds() - RunStart;
	TArray<float> Fr, Gt, Rt, Rh, Gp, Dc, Pr;
	for (const FFrameSample& F : S.Frames)
	{
		Fr.Add(F.FrameMs); Gt.Add(F.GameMs); Rt.Add(F.RenderMs); Rh.Add(F.RHIMs); Gp.Add(F.GPUMs);
		Dc.Add(float(F.DrawCalls)); Pr.Add(float(F.Primitives));
	}
	const float FrMed = NLB::Percentile(Fr, 0.5);
	UE_LOG(LogNLB, Display, TEXT("NLBSEG %s frames=%d fps=%.2f frame=%.3f p95=%.3f game=%.3f render=%.3f rhi=%.3f gpu=%.3f draws=%.0f prims=%.0f"),
		*S.Name, S.Frames.Num(), FrMed > 0 ? 1000.f / FrMed : 0.f, FrMed, NLB::Percentile(Fr, 0.95), NLB::Percentile(Gt, 0.5),
		NLB::Percentile(Rt, 0.5), NLB::Percentile(Rh, 0.5), NLB::Percentile(Gp, 0.5), NLB::Percentile(Dc, 0.5), NLB::Percentile(Pr, 0.5));
}

void UNLBBenchSubsystem::WriteResults(bool bComplete)
{
	// Per-segment table ("asset" is last so readers keyed on the older columns keep working)
	FString Seg = TEXT("tag,mode,layout,count,bookmark,bookmark_name,unlit,frames,start_s,end_s,fps_med,frame_ms_med,frame_ms_mean,frame_ms_p95,game_ms_med,render_ms_med,rhi_ms_med,gpu_ms_med,gpu_ms_p95,drawcalls_med,prims_med,asset\n");
	FString Frames = TEXT("seg,frame_ms,game_ms,render_ms,rhi_ms,gpu_ms,drawcalls,prims\n");
	FString SegJson;
	for (int32 i = 0; i < Segments.Num(); ++i)
	{
		const FSegment& S = Segments[i];
		if (S.Frames.Num() == 0) { continue; }
		TArray<float> Fr, Gt, Rt, Rh, Gp, Dc, Pr;
		for (const FFrameSample& F : S.Frames)
		{
			Fr.Add(F.FrameMs); Gt.Add(F.GameMs); Rt.Add(F.RenderMs); Rh.Add(F.RHIMs); Gp.Add(F.GPUMs);
			Dc.Add(float(F.DrawCalls)); Pr.Add(float(F.Primitives));
			Frames += FString::Printf(TEXT("%s,%.3f,%.3f,%.3f,%.3f,%.3f,%d,%d\n"), *S.Name, F.FrameMs, F.GameMs, F.RenderMs, F.RHIMs, F.GPUMs, F.DrawCalls, F.Primitives);
		}
		const float FrMed = NLB::Percentile(Fr, 0.5);
		Seg += FString::Printf(TEXT("%s,%s,%s,%d,%d,%s,%d,%d,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.0f,%.0f,%s\n"),
			*Tag, *Mode, bISM ? TEXT("ISM") : TEXT("Actors"), S.Count, S.Bookmark, *Bookmarks[S.Bookmark].Name, S.bUnlit ? 1 : 0, S.Frames.Num(),
			S.StartTime, S.EndTime, FrMed > 0 ? 1000.f / FrMed : 0.f, FrMed, NLB::Mean(Fr), NLB::Percentile(Fr, 0.95),
			NLB::Percentile(Gt, 0.5), NLB::Percentile(Rt, 0.5), NLB::Percentile(Rh, 0.5), NLB::Percentile(Gp, 0.5), NLB::Percentile(Gp, 0.95),
			NLB::Percentile(Dc, 0.5), NLB::Percentile(Pr, 0.5), *AssetName);
		SegJson += FString::Printf(TEXT("%s{\"name\":\"%s\",\"count\":%d,\"bookmark\":%d,\"unlit\":%s,\"frames\":%d,\"frame_ms_med\":%.3f,\"gpu_ms_med\":%.3f,\"render_ms_med\":%.3f,\"game_ms_med\":%.3f,\"drawcalls_med\":%.0f,\"prims_med\":%.0f}"),
			SegJson.IsEmpty() ? TEXT("") : TEXT(","), *S.Name, S.Count, S.Bookmark, S.bUnlit ? TEXT("true") : TEXT("false"), S.Frames.Num(),
			FrMed, NLB::Percentile(Gp, 0.5), NLB::Percentile(Rt, 0.5), NLB::Percentile(Gt, 0.5), NLB::Percentile(Dc, 0.5), NLB::Percentile(Pr, 0.5));
	}

	FString Cvars;
	for (const TCHAR* Name : NLB::GInterestingCvars)
	{
		Cvars += FString::Printf(TEXT("%s\"%s\":\"%s\""), Cvars.IsEmpty() ? TEXT("") : TEXT(","), Name, *NLB::JsonEscape(NLB::CvarString(Name)));
	}
	FIntPoint VP(0, 0);
	if (GEngine && GEngine->GameViewport && GEngine->GameViewport->Viewport) { VP = GEngine->GameViewport->Viewport->GetSizeXY(); }

	FString BookmarkJson;
	for (int32 i = 0; i < Bookmarks.Num(); ++i)
	{
		const FBookmark& B = Bookmarks[i];
		BookmarkJson += FString::Printf(TEXT("%s{\"id\":%d,\"name\":\"%s\",\"loc\":[%.1f,%.1f,%.1f],\"rot\":[%.2f,%.2f,%.2f]}"), i ? TEXT(",") : TEXT(""),
			i, *B.Name, B.Location.X, B.Location.Y, B.Location.Z, B.Rotation.Pitch, B.Rotation.Yaw, B.Rotation.Roll);
	}

	// Effective settings (defaults included, which the command line alone does not show)
	FString CountsJson, BookmarkIdsJson;
	for (int32 N : Counts) { CountsJson += FString::Printf(TEXT("%s%d"), CountsJson.IsEmpty() ? TEXT("") : TEXT(","), N); }
	for (int32 B : BookmarkIds) { BookmarkIdsJson += FString::Printf(TEXT("%s%d"), BookmarkIdsJson.IsEmpty() ? TEXT("") : TEXT(","), B); }
	const FString SettingsJson = FString::Printf(
		TEXT("{\"asset\":\"%s\",\"mesh_path\":\"%s\",\"mesh_scale\":%.4f,\"spacing\":%.3f,\"sg\":%d,\"counts\":[%s],\"bookmark_ids\":[%s],")
		TEXT("\"unlit_pass\":%s,\"unlit_rich_view\":%s,\"shots\":%s,\"csv\":%s,\"boot_s\":%.2f,\"spawn_settle_s\":%.2f,\"warm_lap_s\":%.2f,\"settle_s\":%.2f,")
		TEXT("\"settle_min_frames\":%d,\"measure_s\":%.2f,\"max_run_s\":%.0f,\"cvars_requested\":\"%s\",\"out_dir\":\"%s\",\"csv_file\":\"%s\"}"),
		*NLB::JsonEscape(AssetName), *NLB::JsonEscape(MeshPath), MeshScale, SpacingFactor, ScalabilityLevel, *CountsJson, *BookmarkIdsJson,
		bUnlitPass ? TEXT("true") : TEXT("false"), bUnlitRichView ? TEXT("true") : TEXT("false"), bShots ? TEXT("true") : TEXT("false"),
		bCsv ? TEXT("true") : TEXT("false"), BootSec, SpawnSettleSec, WarmLapSec, SettleSec,
		NLB::MinSettleFrames, MeasureSec, MaxRunSec, *NLB::JsonEscape(CvarList), *NLB::JsonEscape(OutDir), *NLB::JsonEscape(CsvFile));

	const FString Summary = FString::Printf(
		TEXT("{\n\"tag\":\"%s\",\"complete\":%s,\"abort_reason\":\"%s\",\"mode\":\"%s\",\"layout\":\"%s\",\"engine\":\"%s\",\n\"rhi\":\"%s\",\"adapter\":\"%s\",\"driver\":\"%s\",\"use_nanite\":%s,\"platform_supports_nanite\":%s,\"atomics64\":%s,\n")
		TEXT("\"viewport\":[%d,%d],\"grid_half_cm\":%.1f,\"cell_cm\":%.1f,\n\"settings\":%s,\n\"mesh\":%s,\n\"spawns\":[%s],\n\"nanite_proxies\":[%s],\n\"cvars\":{%s},\n\"bookmarks\":[%s],\n\"segments\":[%s],\n\"cmdline\":\"%s\",\"run_s\":%.1f\n}\n"),
		*Tag, bComplete ? TEXT("true") : TEXT("false"), *NLB::JsonEscape(AbortReason), *Mode, bISM ? TEXT("ISM") : TEXT("Actors"), *NLB::JsonEscape(FEngineVersion::Current().ToString()),
		GDynamicRHI ? GDynamicRHI->GetName() : TEXT("?"), *NLB::JsonEscape(GRHIAdapterName), *NLB::JsonEscape(GRHIAdapterUserDriverVersion),
		UseNanite(GMaxRHIShaderPlatform) ? TEXT("true") : TEXT("false"), DoesPlatformSupportNanite(GMaxRHIShaderPlatform) ? TEXT("true") : TEXT("false"),
		GRHISupportsAtomicUInt64 ? TEXT("true") : TEXT("false"), VP.X, VP.Y, GridHalf, CellSize.X, *SettingsJson, *MeshInfoJson,
		*FString::Join(SpawnLog, TEXT(",")), *FString::Join(NaniteProxyLog, TEXT(",")), *Cvars, *BookmarkJson, *SegJson,
		*NLB::JsonEscape(FCommandLine::Get()), FPlatformTime::Seconds() - RunStart);

	const auto Enc = FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM;
	FFileHelper::SaveStringToFile(Seg, *FPaths::Combine(OutDir, Tag + TEXT("_segments.csv")), Enc);
	FFileHelper::SaveStringToFile(Frames, *FPaths::Combine(OutDir, Tag + TEXT("_frames.csv")), Enc);
	FFileHelper::SaveStringToFile(Summary, *FPaths::Combine(OutDir, Tag + TEXT("_summary.json")), Enc);
	UE_LOG(LogNLB, Display, TEXT("NLB results written to %s (%d segments, complete=%d)"), *OutDir, Segments.Num(), bComplete ? 1 : 0);
}

void UNLBBenchSubsystem::Tick(float DeltaTime)
{
	Super::Tick(DeltaTime);
	if (!bActive) { return; }
	++PhaseFrames;

	// Backstop for a run that cannot finish (e.g. the flight harness died and the game would keep the box under load until it resets).
	if (MaxRunSec > 0.0 && Phase != EPhase::Finish && Phase != EPhase::Exiting && FPlatformTime::Seconds() - RunStart > MaxRunSec)
	{
		AbortRun(TEXT("max_run"));
		AbortExitCode = 4;
		EnterPhase(EPhase::Exiting);
		return;
	}

	auto NextStep = [this]()
	{
		++SegIndex;
		if (!Segments.IsValidIndex(SegIndex))
		{
			EnterPhase(EPhase::Finish);
			return;
		}
		const FSegment& S = Segments[SegIndex];
		if (S.Count > SpawnedCount)
		{
			EnterPhase(EPhase::Spawn);
		}
		else if (S.bUnlit != bUnlitActive)
		{
			SetUnlit(S.bUnlit);
			WarmLapIndex = 0;
			SetBookmark(BookmarkIds[0]);
			EnterPhase(EPhase::WarmLap);
		}
		else
		{
			SetBookmark(S.Bookmark);
			EnterPhase(EPhase::Settle);
		}
	};

	switch (Phase)
	{
	case EPhase::Boot:
		if (!EnsureCamera())
		{
			if (PhaseAge() >= BootSec + 60.0)
			{
				AbortRun(TEXT("no_player_camera"));
				AbortExitCode = 5;
				EnterPhase(EPhase::Exiting);
			}
			return;
		}
		if (PhaseAge() >= BootSec)
		{
#if CSV_PROFILER
			if (bCsv && FCsvProfiler::Get())
			{
				FCsvProfiler::Get()->BeginCapture(-1, OutDir, Tag + TEXT("_profile.csv"));
				bCsvStarted = true;
				CSV_METADATA(TEXT("NLBTag"), *Tag);
				CSV_METADATA(TEXT("NLBAsset"), *AssetName);
				CSV_METADATA(TEXT("NLBMode"), *Mode);
				CSV_METADATA(TEXT("NLBLayout"), bISM ? TEXT("ISM") : TEXT("Actors"));
			}
#endif
			NextStep();
		}
		break;

	case EPhase::Spawn:
		EnsureCamera();
		SpawnUpTo(Segments[SegIndex].Count);
		EnterPhase(EPhase::SpawnSettle);
		break;

	case EPhase::SpawnSettle:
		if (PhaseAge() >= SpawnSettleSec)
		{
			const int32 NaniteN = CountNaniteProxies();
			NaniteProxyLog.Add(FString::Printf(TEXT("{\"count\":%d,\"nanite_proxies\":%d}"), SpawnedCount, NaniteN));
			UE_LOG(LogNLB, Display, TEXT("NLB nanite proxies %d of %d copies"), NaniteN, SpawnedCount);
			WarmLapIndex = 0;
			SetBookmark(BookmarkIds[0]);
			EnterPhase(EPhase::WarmLap);
		}
		break;

	case EPhase::WarmLap:
		if (PhaseAge() >= WarmLapSec)
		{
			++WarmLapIndex;
			if (WarmLapIndex < BookmarkIds.Num())
			{
				SetBookmark(BookmarkIds[WarmLapIndex]);
				PhaseStart = FPlatformTime::Seconds();
			}
			else
			{
				SetBookmark(Segments[SegIndex].Bookmark);
				EnterPhase(EPhase::Settle);
			}
		}
		break;

	case EPhase::Settle:
		if (PhaseAge() >= SettleSec && PhaseFrames >= NLB::MinSettleFrames)
		{
			// This frame's GC would land in the first Measure sample (its delta and GGameThreadTime span this frame's end).
			if (GEngine) { GEngine->DelayGarbageCollection(); }
			Segments[SegIndex].StartTime = FPlatformTime::Seconds() - RunStart;
#if CSV_PROFILER
			CSV_EVENT(NLB, TEXT("%s"), *Segments[SegIndex].Name);
#endif
			TRACE_BOOKMARK(TEXT("NLB %s"), *Segments[SegIndex].Name);
			EnterPhase(EPhase::Measure);
		}
		break;

	case EPhase::Measure:
		// No time-based GC pass inside a measure window: the subsystem ticks before this frame's ConditionalCollectGarbage
		// (LevelTick.cpp:1821 vs :1970), and a set flag skips that frame's incremental GC step (UnrealEngine.cpp:2272-2275).
		// With 8192 actors a reachability pass is a multi-ms game-thread hitch; it now runs in the next Settle/Shot instead
		// (a sample taken at tick N carries frame N-1's GC, hence the extra delay on the Settle -> Measure tick above).
		if (GEngine) { GEngine->DelayGarbageCollection(); }
		SampleFrame(DeltaTime);
		if (PhaseAge() >= MeasureSec)
		{
#if CSV_PROFILER
			CSV_EVENT(NLB, TEXT("%s_end"), *Segments[SegIndex].Name);
#endif
			FinishSegment();
			const FSegment& S = Segments[SegIndex];
			if (bShots && S.Count == Counts.Last())
			{
				const FString Shot = FPaths::Combine(OutDir, Tag + TEXT("_shots"), S.Name + TEXT(".png"));
				FScreenshotRequest::RequestScreenshot(Shot, /*bShowUI*/ false, /*bAddFilenameSuffix*/ false);
				EnterPhase(EPhase::Shot);
			}
			else
			{
				NextStep();
			}
		}
		break;

	case EPhase::Shot:
		if (PhaseAge() >= 1.0) { NextStep(); }
		break;

	case EPhase::Finish:
#if CSV_PROFILER
		if (bCsvStarted && FCsvProfiler::Get())
		{
			CsvFile = FCsvProfiler::Get()->GetOutputFilename();
			FCsvProfiler::Get()->EndCapture();
		}
#endif
		WriteResults(/*bComplete*/ true);
		UE_LOG(LogNLB, Display, TEXT("NLB_DONE tag=%s segments=%d run_s=%.1f"), *Tag, Segments.Num(), FPlatformTime::Seconds() - RunStart);
		EnterPhase(EPhase::Exiting);
		break;

	case EPhase::Exiting:
	{
		bool bWriting = false;
#if CSV_PROFILER
		// The stop is only taken one EndFrame after the request, and IsWritingFile() turns true one frame later still
		// (CsvProfiler.cpp:4225-4241), so below ~1 fps the 2 s floor alone could exit before the file write even started.
		if (bCsvStarted && FCsvProfiler::Get())
		{
			const FCsvProfiler* NLBCsvProf = FCsvProfiler::Get();
			bWriting = FCsvProfiler::IsCapturing() || NLBCsvProf->IsEndCapturePending() || NLBCsvProf->IsWritingFile();
		}
#endif
		if ((!bWriting && PhaseAge() >= 2.0) || PhaseAge() >= 30.0)
		{
			bActive = false;
			if (!bNoExit)
			{
				if (AbortExitCode != 0) { FPlatformMisc::RequestExitWithStatus(false, AbortExitCode, TEXT("NLB aborted")); }
				else { FPlatformMisc::RequestExit(false, TEXT("NLB done")); }
			}
		}
		break;
	}

	default:
		break;
	}
}

void UNLBBenchSubsystem::AbortRun(const TCHAR* Reason)
{
	AbortReason = Reason;
#if CSV_PROFILER
	if (bCsvStarted && FCsvProfiler::Get() && FCsvProfiler::Get()->IsCapturing())
	{
		CsvFile = FCsvProfiler::Get()->GetOutputFilename();
		FCsvProfiler::Get()->EndCapture();
	}
#endif
	UE_LOG(LogNLB, Warning, TEXT("NLB_ABORTED tag=%s segment=%d of %d run_s=%.1f reason=%s"), *Tag, SegIndex, Segments.Num(), FPlatformTime::Seconds() - RunStart, Reason);
	WriteResults(/*bComplete*/ false);
}

void UNLBBenchSubsystem::Deinitialize()
{
	if (bActive && Phase != EPhase::Finish && Phase != EPhase::Exiting)
	{
		// Cut short (window closed, harness timeout/CloseMainWindow, crash-free exit): still leave valid result files, marked
		// "complete":false, instead of none at all.
		AbortRun(TEXT("deinitialize"));
	}
	bActive = false;
	Super::Deinitialize();
}

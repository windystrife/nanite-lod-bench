#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "NLBGameMode.generated.h"

/**
 * Game mode for the bench: no possessed pawn. The PC stays in NAME_Spectating (PlayerController.cpp:1090), so ReceivedPlayer
 * gives it a spectator pawn (:944-948) that has no mesh (SpectatorPawn.cpp:15); the UNLBBenchSubsystem turns
 * bAutoManageActiveCameraTarget off and owns the view target camera.
 */
UCLASS()
class ANLBGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ANLBGameMode();

	/** Never spawn a pawn: the bench drives an ACameraActor as the view target. */
	virtual void RestartPlayer(AController* NewPlayer) override {}
};

#pragma once

#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"
#include "TimerManager.h"
#include "AutoYouGameInputSubsystem.generated.h"

class IWebSocket;

USTRUCT(BlueprintType)
struct AUTOYOUGAMEINPUT_API FAutoYouTouchPoint
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") int32 Id = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FVector2D Position = FVector2D::ZeroVector;
};

USTRUCT(BlueprintType)
struct AUTOYOUGAMEINPUT_API FAutoYouGameFrame
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString SessionId;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Event;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString InputType;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Button;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Phase;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Axis;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Sensor;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Action;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Key;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString Text;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString KeyboardState;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") FString CoordinateMode;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") bool bHasMousePosition = false;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") float X = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") float Y = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") float Z = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") float Dx = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") float Dy = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") float Value = 0;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") TArray<FAutoYouTouchPoint> Points;
};

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAutoYouGameFrameEvent, const FAutoYouGameFrame&, Frame);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAutoYouGameSessionEvent, const FString&, SessionId);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAutoYouGameConnectionEvent, bool, bIsConnected);

UCLASS()
class AUTOYOUGAMEINPUT_API UAutoYouGameInputSubsystem : public UGameInstanceSubsystem
{
    GENERATED_BODY()

public:
    UPROPERTY(BlueprintAssignable, Category="AutoYou Game Input") FAutoYouGameFrameEvent OnFrame;
    UPROPERTY(BlueprintAssignable, Category="AutoYou Game Input") FAutoYouGameSessionEvent OnSessionReset;
    UPROPERTY(BlueprintAssignable, Category="AutoYou Game Input") FAutoYouGameConnectionEvent OnConnectionChanged;
    UPROPERTY(BlueprintReadOnly, Category="AutoYou Game Input") bool bConnected = false;

    // Reads AUTOYOU_GAME_STREAM_TOKEN from the host process environment.
    UFUNCTION(BlueprintCallable, Category="AutoYou Game Input") bool Start(int32 Port = 8001);
    UFUNCTION(BlueprintCallable, Category="AutoYou Game Input") void Stop();
    virtual void Deinitialize() override;

private:
    void Connect();
    void HandleConnected(uint64 Epoch);
    void HandleMessage(uint64 Epoch, const FString& Message);
    void DisconnectAndRetry(uint64 Epoch);
    void CloseSocket();
    void ResetSessions();

    TSharedPtr<IWebSocket> Socket;
    TSet<FString> Sessions;
    FTimerHandle RetryTimer;
    FString Token;
    int32 ServerPort = 8001;
    float RetrySeconds = 0.5f;
    uint64 SocketEpoch = 0;
    bool bRunning = false;
};

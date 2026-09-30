#include "AutoYouGameInputSubsystem.h"

#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "Engine/GameInstance.h"
#include "Engine/World.h"
#include "HAL/PlatformMisc.h"
#include "IWebSocket.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "WebSocketsModule.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, AutoYouGameInput)

bool UAutoYouGameInputSubsystem::Start(int32 Port)
{
    Stop();
    if (Port < 1 || Port > 65535)
    {
        return false;
    }
    Token = FPlatformMisc::GetEnvironmentVariable(TEXT("AUTOYOU_GAME_STREAM_TOKEN"));
    if (Token.IsEmpty())
    {
        return false;
    }
    ServerPort = Port;
    RetrySeconds = 0.5f;
    bRunning = true;
    Connect();
    return true;
}

void UAutoYouGameInputSubsystem::Stop()
{
    bRunning = false;
    ++SocketEpoch;
    if (UWorld* World = GetGameInstance() ? GetGameInstance()->GetWorld() : nullptr)
    {
        World->GetTimerManager().ClearTimer(RetryTimer);
    }
    CloseSocket();
    ResetSessions();
    if (bConnected)
    {
        bConnected = false;
        OnConnectionChanged.Broadcast(false);
    }
    Token.Empty();
}

void UAutoYouGameInputSubsystem::Deinitialize()
{
    Stop();
    Super::Deinitialize();
}

void UAutoYouGameInputSubsystem::Connect()
{
    if (!bRunning)
    {
        return;
    }
    TMap<FString, FString> Headers;
    Headers.Add(TEXT("Authorization"), TEXT("Bearer ") + Token);
    const FString Url = FString::Printf(TEXT("ws://127.0.0.1:%d/api/webrtc/game-input/stream"), ServerPort);
    Socket = FWebSocketsModule::Get().CreateWebSocket(Url, TArray<FString>(), Headers);
    const uint64 Epoch = ++SocketEpoch;
    const TWeakObjectPtr<UAutoYouGameInputSubsystem> Weak(this);
    Socket->OnConnected().AddLambda([Weak, Epoch]() {
        if (Weak.IsValid()) Weak->HandleConnected(Epoch);
    });
    Socket->OnMessage().AddLambda([Weak, Epoch](const FString& Message) {
        if (Weak.IsValid()) Weak->HandleMessage(Epoch, Message);
    });
    Socket->OnClosed().AddLambda([Weak, Epoch](int32, const FString&, bool) {
        if (Weak.IsValid()) Weak->DisconnectAndRetry(Epoch);
    });
    Socket->OnConnectionError().AddLambda([Weak, Epoch](const FString&) {
        if (Weak.IsValid()) Weak->DisconnectAndRetry(Epoch);
    });
    Socket->Connect();
}

void UAutoYouGameInputSubsystem::HandleConnected(uint64 Epoch)
{
    if (!IsInGameThread())
    {
        const TWeakObjectPtr<UAutoYouGameInputSubsystem> Weak(this);
        AsyncTask(ENamedThreads::GameThread, [Weak, Epoch]() { if (Weak.IsValid()) Weak->HandleConnected(Epoch); });
        return;
    }
    if (!bRunning || Epoch != SocketEpoch) return;
    RetrySeconds = 0.5f;
    bConnected = true;
    OnConnectionChanged.Broadcast(true);
}

void UAutoYouGameInputSubsystem::HandleMessage(uint64 Epoch, const FString& Message)
{
    if (!IsInGameThread())
    {
        const TWeakObjectPtr<UAutoYouGameInputSubsystem> Weak(this);
        AsyncTask(ENamedThreads::GameThread, [Weak, Epoch, Message]() { if (Weak.IsValid()) Weak->HandleMessage(Epoch, Message); });
        return;
    }
    if (!bRunning || Epoch != SocketEpoch || Message.Len() > 16384)
    {
        return;
    }
    TSharedPtr<FJsonObject> Json;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Message), Json) || !Json.IsValid())
    {
        return;
    }
    FString Event;
    if (!Json->TryGetStringField(TEXT("event"), Event) || Event == TEXT("heartbeat"))
    {
        return;
    }
    FString SessionId;
    if (!Json->TryGetStringField(TEXT("session_id"), SessionId) || SessionId.IsEmpty() || SessionId.Len() > 128)
    {
        return;
    }
    FString InputType;
    Json->TryGetStringField(TEXT("input_type"), InputType);
    if (Event == TEXT("game_input") && InputType == TEXT("state_reset"))
    {
        bool bAllSessions = false;
        if (Json->TryGetBoolField(TEXT("all_sessions"), bAllSessions) && bAllSessions)
        {
            ResetSessions();
        }
        return;
    }
    if (Event == TEXT("game_input") && InputType == TEXT("session_end"))
    {
        Sessions.Remove(SessionId);
        OnSessionReset.Broadcast(SessionId);
        return;
    }
    if (Event != TEXT("game_input") && Event != TEXT("remote_desktop_input")
        && Event != TEXT("remote_desktop_keyboard"))
    {
        return;
    }
    if (!Sessions.Contains(SessionId) && Sessions.Num() >= 128)
    {
        ResetSessions();
    }
    Sessions.Add(SessionId);
    FAutoYouGameFrame Frame;
    Frame.SessionId = SessionId;
    Frame.Event = Event;
    Frame.InputType = InputType;
    Json->TryGetStringField(TEXT("button"), Frame.Button);
    Json->TryGetStringField(TEXT("phase"), Frame.Phase);
    Json->TryGetStringField(TEXT("axis"), Frame.Axis);
    Json->TryGetStringField(TEXT("sensor"), Frame.Sensor);
    Json->TryGetStringField(TEXT("action"), Frame.Action);
    Json->TryGetStringField(TEXT("key"), Frame.Key);
    Json->TryGetStringField(TEXT("text"), Frame.Text);
    Json->TryGetStringField(TEXT("keyboard_state"), Frame.KeyboardState);
    Json->TryGetStringField(TEXT("coordinate_mode"), Frame.CoordinateMode);
    double Number = 0;
    const bool bHasX = Json->TryGetNumberField(TEXT("x"), Number);
    if (bHasX) Frame.X = static_cast<float>(Number);
    const bool bHasY = Json->TryGetNumberField(TEXT("y"), Number);
    if (bHasY) Frame.Y = static_cast<float>(Number);
    Frame.bHasMousePosition = Event == TEXT("remote_desktop_input") && bHasX && bHasY;
    if (Json->TryGetNumberField(TEXT("z"), Number)) Frame.Z = static_cast<float>(Number);
    if (Json->TryGetNumberField(TEXT("dx"), Number)) Frame.Dx = static_cast<float>(Number);
    if (Json->TryGetNumberField(TEXT("dy"), Number)) Frame.Dy = static_cast<float>(Number);
    if (Json->TryGetNumberField(TEXT("value"), Number)) Frame.Value = static_cast<float>(Number);
    const TArray<TSharedPtr<FJsonValue>>* Points = nullptr;
    if (Json->TryGetArrayField(TEXT("points"), Points) && Points->Num() <= 10)
    {
        for (const TSharedPtr<FJsonValue>& PointValue : *Points)
        {
            const TSharedPtr<FJsonObject> PointJson = PointValue.IsValid() ? PointValue->AsObject() : nullptr;
            if (!PointJson.IsValid()) continue;
            FAutoYouTouchPoint Point;
            if (PointJson->TryGetNumberField(TEXT("id"), Number)) Point.Id = static_cast<int32>(Number);
            if (PointJson->TryGetNumberField(TEXT("x"), Number)) Point.Position.X = static_cast<float>(Number);
            if (PointJson->TryGetNumberField(TEXT("y"), Number)) Point.Position.Y = static_cast<float>(Number);
            Frame.Points.Add(Point);
        }
    }
    OnFrame.Broadcast(Frame);
}

void UAutoYouGameInputSubsystem::DisconnectAndRetry(uint64 Epoch)
{
    if (!IsInGameThread())
    {
        const TWeakObjectPtr<UAutoYouGameInputSubsystem> Weak(this);
        AsyncTask(ENamedThreads::GameThread, [Weak, Epoch]() { if (Weak.IsValid()) Weak->DisconnectAndRetry(Epoch); });
        return;
    }
    if (!bRunning || Epoch != SocketEpoch) return;
    ++SocketEpoch;
    CloseSocket();
    ResetSessions();
    if (bConnected)
    {
        bConnected = false;
        OnConnectionChanged.Broadcast(false);
    }
    if (bRunning)
    {
        if (UWorld* World = GetGameInstance() ? GetGameInstance()->GetWorld() : nullptr)
        {
            World->GetTimerManager().SetTimer(RetryTimer, this, &UAutoYouGameInputSubsystem::Connect, RetrySeconds);
            RetrySeconds = FMath::Min(RetrySeconds * 2.0f, 5.0f);
        }
    }
}

void UAutoYouGameInputSubsystem::CloseSocket()
{
    if (!Socket.IsValid()) return;
    Socket->OnConnected().Clear();
    Socket->OnMessage().Clear();
    Socket->OnClosed().Clear();
    Socket->OnConnectionError().Clear();
    Socket->Close();
    Socket.Reset();
}

void UAutoYouGameInputSubsystem::ResetSessions()
{
    TSet<FString> Pending = MoveTemp(Sessions);
    Sessions.Empty();
    for (const FString& SessionId : Pending)
    {
        OnSessionReset.Broadcast(SessionId);
    }
}

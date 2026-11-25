using UnrealBuildTool;

public class AutoYouGameInput : ModuleRules
{
    public AutoYouGameInput(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] { "Json", "WebSockets" });
    }
}

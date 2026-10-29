# Material.renderQueue

public int renderQueue ;

Description
Render queue of this material.

By default materials use render queue of the shader it uses. You can override
the render queue used using this variable. Note that if a shader on the material is changed, the render queue resets to that of the shader itself.

Render queue value should be in [0..5000] range to work properly; or -1 to use the render queue from the shader.

Note: When Unity runs in batch mode, it does not load Scriptable Render Pipelines (SRPs) until the first time something renders. Loading an SRP modifies the sub-shader selected for a given material which can lead to the value this function returns being different than expected.

Additional resources: Shader.renderQueue , RenderQueue enum, subshader tags .

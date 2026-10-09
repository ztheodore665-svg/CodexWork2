using UnityEngine;

namespace PriestsAndDevils.Views
{
    public static class RuntimeSpriteFactory
    {
        private static readonly Color Transparent = new Color(0, 0, 0, 0);

        public static Sprite CreateSquareSprite(string name, Color color, int pixels = 64)
        {
            Texture2D texture = new Texture2D(pixels, pixels, TextureFormat.RGBA32, false)
            {
                name = name + "Texture",
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp
            };
            Color[] colors = new Color[pixels * pixels];
            for (int i = 0; i < colors.Length; i++) colors[i] = color;
            texture.SetPixels(colors);
            texture.Apply();
            return Sprite.Create(texture, new Rect(0, 0, pixels, pixels), new Vector2(0.5f, 0.5f), pixels, 0, SpriteMeshType.FullRect);
        }

        public static Sprite CreateCircleSprite(string name, Color color, int pixels = 96)
        {
            Texture2D texture = new Texture2D(pixels, pixels, TextureFormat.RGBA32, false)
            {
                name = name + "Texture",
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp
            };
            Vector2 center = new Vector2((pixels - 1) * 0.5f, (pixels - 1) * 0.5f);
            float radius = pixels * 0.48f;
            Color[] colors = new Color[pixels * pixels];
            for (int y = 0; y < pixels; y++)
            {
                for (int x = 0; x < pixels; x++)
                {
                    float distance = Vector2.Distance(new Vector2(x, y), center);
                    colors[y * pixels + x] = distance <= radius ? color : Transparent;
                }
            }
            texture.SetPixels(colors);
            texture.Apply();
            return Sprite.Create(texture, new Rect(0, 0, pixels, pixels), new Vector2(0.5f, 0.5f), pixels, 0, SpriteMeshType.Tight);
        }
    }
}

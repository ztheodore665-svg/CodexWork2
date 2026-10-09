using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class BoatView : MonoBehaviour
    {
        public void Initialize(Sprite sprite)
        {
            SpriteRenderer renderer = gameObject.GetComponent<SpriteRenderer>();
            if (renderer == null) renderer = gameObject.AddComponent<SpriteRenderer>();
            renderer.sprite = sprite;
            renderer.color = new Color(0.32f, 0.13f, 0.06f);
        }
    }
}

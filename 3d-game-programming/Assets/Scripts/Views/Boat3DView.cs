using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class Boat3DView : MonoBehaviour
    {
        public void Initialize(Color color)
        {
            Renderer renderer = GetComponent<Renderer>();
            if (renderer == null) renderer = gameObject.AddComponent<MeshRenderer>();
            renderer.material.color = color;
        }
    }
}
